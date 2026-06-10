"""Background WebSocket coordinator for a SolarAssistant config entry."""
from __future__ import annotations

import asyncio
import logging
import socket as _socket
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.dt import utcnow

from py_solar_assistant import (
    ConnectError,
    DeviceMetric,
    Metric,
    Options,
    SolarAssistantClient,
    SolarAssistantError,
    TopicFilter,
    authorize_site,
    connect,
    get_device_metrics,
    set_metric,
)
from .const import (
    AUTH_CLOUD,
    AUTH_LOCAL,
    CLOUD_IP_REFRESH_INTERVAL_S,
    CONF_API_KEY,
    CONF_AUTH_METHOD,
    CONF_ENABLED_TOPICS,
    CONF_HOST,
    CONF_LOCAL_IP,
    CONF_PASSWORD,
    CONF_SITE_ID,
    CONF_SITE_KEY,
    CONF_TOKEN,
    DEFAULT_CURATED_GLOBS,
    IP_RESCAN_AFTER_S,
    IP_RESCAN_INTERVAL_S,
    MDNS_SCAN_TIMEOUT_S,
    MDNS_SERVICE_TYPE,
    RECONNECT_INITIAL_S,
    RECONNECT_MAX_S,
    signal_connection_state,
    signal_metric_update,
    signal_new_metric,
)

_LOGGER = logging.getLogger(__name__)


class SolarAssistantCoordinator:
    """Owns one WebSocket per config entry, streams metrics into HA."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.definitions: dict[str, dict[str, Any]] = {}
        self.data: dict[str, Any] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False
        # Connection state
        self.is_connected: bool = False
        self.connected_host: str | None = None
        self.last_connected_at: datetime | None = None
        self.last_error: str | None = None
        # IP-change recovery
        self._first_failure_at: datetime | None = None
        self._last_ip_scan_at: datetime | None = None
        self._last_cloud_auth_at: datetime | None = None

    @property
    def signal_new_metric(self) -> str:
        return signal_new_metric(self.entry.entry_id)

    @property
    def signal_metric_update(self) -> str:
        return signal_metric_update(self.entry.entry_id)

    @property
    def signal_connection_state(self) -> str:
        return signal_connection_state(self.entry.entry_id)

    def _set_connection_state(
        self, connected: bool, *, host: str | None = None, error: str | None = None
    ) -> None:
        self.is_connected = connected
        if host is not None:
            self.connected_host = host
        if connected:
            self.last_connected_at = utcnow()
            self.last_error = None
        elif error is not None:
            self.last_error = error
        async_dispatcher_send(self.hass, self.signal_connection_state)

    async def async_start(self) -> None:
        """Spawn the background reconnect loop. Returns immediately."""
        self._task = self.hass.async_create_background_task(
            self._run(), f"solar_assistant_ws[{self.entry.entry_id}]"
        )

    async def async_stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        """Reconnect loop with exponential backoff."""
        backoff = RECONNECT_INITIAL_S
        while not self._stopping:
            try:
                await self._session_once()
                backoff = RECONNECT_INITIAL_S
                self._first_failure_at = None
            except asyncio.CancelledError:
                return
            except _ReauthRequired as err:
                _LOGGER.info("Re-authorizing site %s: %s", self.entry.title, err)
                self._set_connection_state(False, error=str(err))
                try:
                    await self._refresh_cloud_token()
                    backoff = RECONNECT_INITIAL_S
                    self._first_failure_at = None
                    continue
                except Exception as auth_err:
                    _LOGGER.warning("Re-authorize failed: %s", auth_err)
                    self._set_connection_state(False, error=f"re-authorize failed: {auth_err}")
            except Exception as err:
                _LOGGER.warning(
                    "SolarAssistant connection lost (%s) — retrying in %ss",
                    err, backoff,
                )
                self._set_connection_state(False, error=str(err))
                now = utcnow()
                if self._first_failure_at is None:
                    self._first_failure_at = now
                elapsed = (now - self._first_failure_at).total_seconds()
                if elapsed >= IP_RESCAN_AFTER_S:
                    if (self._last_ip_scan_at is None or
                            (now - self._last_ip_scan_at).total_seconds() >= IP_RESCAN_INTERVAL_S):
                        self._last_ip_scan_at = now
                        await self._recover_ip()

            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, RECONNECT_MAX_S)

    async def _session_once(self) -> None:
        opts = self._build_options()

        # Pre-populate definitions and data from REST before connecting the WebSocket.
        # Settings topics (number/select/switch) are not in the curated WS filter, so
        # without this they would never appear as entities.
        await self._rest_discovery()

        try:
            sock = await connect(opts)
        except ConnectError as err:
            msg = str(err)
            if ("401" in msg or "403" in msg) and self.entry.data.get(CONF_AUTH_METHOD) == AUTH_CLOUD:
                raise _ReauthRequired(msg) from err
            raise

        try:
            filters = self._build_topic_filters()
            await sock.subscribe_metrics(self._on_metric, *filters)
            self._set_connection_state(True, host=sock.connected_host)
            await sock.listen()
        finally:
            self._set_connection_state(False)
            try:
                await sock.close()
            except Exception:
                pass

    async def _rest_discovery(self) -> None:
        """Fetch all metric definitions + current values from REST."""
        try:
            metrics = await self._fetch_discovery()
        except Exception as err:
            _LOGGER.debug("REST discovery skipped: %s", err)
            return
        for m in metrics:
            if m.group == "Ignore":
                continue
            topic = m.topic
            is_new = topic not in self.definitions
            self.definitions[topic] = _metric_defn(m)
            self.data[topic] = m.value
            if is_new:
                async_dispatcher_send(self.hass, self.signal_new_metric, topic)
            elif self.definitions[topic].get("platform") in ("number", "select", "switch"):
                async_dispatcher_send(self.hass, self.signal_metric_update, topic)

    async def _fetch_discovery(self) -> list[DeviceMetric]:
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        if method == AUTH_LOCAL:
            return await get_device_metrics(d[CONF_HOST], password=d[CONF_PASSWORD])
        local_ip = d.get(CONF_LOCAL_IP, "")
        token = d[CONF_TOKEN]
        if local_ip:
            try:
                return await get_device_metrics(local_ip, token=token)
            except Exception as err:
                _LOGGER.debug("Local REST discovery to %s failed: %s", local_ip, err)
        return await get_device_metrics(d.get(CONF_HOST, ""), token=token, scheme="https")

    async def _recover_ip(self) -> None:
        """Try to find the unit's new IP after prolonged connection failure."""
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        site_id = d.get(CONF_SITE_ID)

        if site_id is None:
            # TODO: fetch site_id from local REST API once SA backend exposes it,
            # so IP recovery works for local-password entries added before mDNS was available.
            _LOGGER.debug("IP recovery skipped: no site_id stored for this entry")
            return

        _LOGGER.info("IP recovery: scanning mDNS for site_id=%s", site_id)
        new_ip = await self._mdns_find_ip(int(site_id))
        if new_ip:
            _LOGGER.info("IP recovery: found %s via mDNS, updating stored host", new_ip)
            self._update_host(new_ip)
            return

        # mDNS found nothing — for cloud entries try re-authorizing (max once per 12 h)
        if method == AUTH_CLOUD:
            now = utcnow()
            if (self._last_cloud_auth_at is None or
                    (now - self._last_cloud_auth_at).total_seconds() >= CLOUD_IP_REFRESH_INTERVAL_S):
                self._last_cloud_auth_at = now
                _LOGGER.info("IP recovery: refreshing cloud token to get updated local IP")
                try:
                    await self._refresh_cloud_token()
                except Exception as err:
                    _LOGGER.debug("IP recovery cloud refresh failed: %s", err)

    def _update_host(self, ip: str) -> None:
        """Persist a newly discovered IP into the config entry."""
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        key = CONF_HOST if method == AUTH_LOCAL else CONF_LOCAL_IP
        self.hass.config_entries.async_update_entry(self.entry, data={**d, key: ip})

    async def _mdns_find_ip(self, site_id: int) -> str | None:
        from homeassistant.components.zeroconf import async_get_instance
        try:
            zc = await async_get_instance(self.hass)
            return await self.hass.async_add_executor_job(_mdns_scan_for_site, zc, site_id)
        except Exception as err:
            _LOGGER.debug("mDNS scan failed: %s", err)
            return None

    def _build_options(self) -> Options:
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        if method == AUTH_LOCAL:
            return Options(local_ip=d[CONF_HOST], password=d[CONF_PASSWORD])
        return Options(
            host=d.get(CONF_HOST, ""),
            local_ip=d.get(CONF_LOCAL_IP, ""),
            token=d.get(CONF_TOKEN, ""),
            site_id=int(d.get(CONF_SITE_ID, 0) or 0),
            site_key=d.get(CONF_SITE_KEY, ""),
        )

    def _build_topic_filters(self) -> list[TopicFilter]:
        """Server-side subscription list. Always includes settings topics for live updates."""
        explicit = self.entry.options.get(CONF_ENABLED_TOPICS)
        base: list[str] = list(explicit) if explicit is not None else list(DEFAULT_CURATED_GLOBS)
        base_set = set(base)
        for topic, defn in self.definitions.items():
            if defn.get("platform") in ("number", "select", "switch") and topic not in base_set:
                base.append(topic)
        return [TopicFilter(topic=t) for t in base]

    async def set_setting(self, topic: str, value: Any) -> None:
        """Push a Settings-group value back to the SolarAssistant unit via REST."""
        d = self.entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        try:
            if method == AUTH_LOCAL:
                await set_metric(
                    d[CONF_HOST],
                    topic,
                    str(value),
                    password=d[CONF_PASSWORD],
                )
            else:
                local_ip = d.get(CONF_LOCAL_IP, "")
                token = d[CONF_TOKEN]
                if local_ip and self.connected_host == local_ip:
                    await set_metric(local_ip, topic, str(value), token=token)
                else:
                    await set_metric(
                        d[CONF_HOST],
                        topic,
                        str(value),
                        token=token,
                        scheme="https",
                        site_id=int(d.get(CONF_SITE_ID, 0) or 0),
                        site_key=d.get(CONF_SITE_KEY, ""),
                    )
        except SolarAssistantError as err:
            raise HomeAssistantError(str(err)) from err

    async def _refresh_cloud_token(self) -> None:
        d = self.entry.data
        api_key = d[CONF_API_KEY]
        site_id = int(d[CONF_SITE_ID])
        async with SolarAssistantClient(api_key) as client:
            auth = await authorize_site(client, site_id)
        new_data = {
            **d,
            CONF_HOST: auth.host,
            CONF_LOCAL_IP: auth.local_ip,
            CONF_TOKEN: auth.token,
            CONF_SITE_ID: auth.site_id,
            CONF_SITE_KEY: auth.site_key,
        }
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    @callback
    def _on_metric(self, m: Metric) -> None:
        if m.group == "Ignore":
            return
        topic = m.topic
        is_new = topic not in self.definitions
        self.definitions[topic] = _metric_defn(m)
        self.data[topic] = m.value
        if is_new:
            async_dispatcher_send(self.hass, self.signal_new_metric, topic)
        async_dispatcher_send(self.hass, self.signal_metric_update, topic)


class _ReauthRequired(Exception):
    """Internal: cloud token rejected, refresh needed."""


def _mdns_scan_for_site(zc: Any, site_id: int) -> str | None:
    """Blocking: browse _solar-assistant._tcp using an existing Zeroconf instance."""
    import time
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange

    found: list[str] = []

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        if state_change != ServiceStateChange.Added or found:
            return
        info = ServiceInfo(service_type, name)
        if not info.request(zeroconf, timeout=2000):
            return
        props = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        if str(props.get("site_id", "")) == str(site_id) and info.addresses:
            found.append(_socket.inet_ntoa(info.addresses[0]))

    browser = ServiceBrowser(zc, MDNS_SERVICE_TYPE, handlers=[on_change])
    time.sleep(MDNS_SCAN_TIMEOUT_S)
    browser.cancel()
    return found[0] if found else None


def _metric_defn(m: Any) -> dict[str, Any]:
    """Build a definition dict from either a Metric (WebSocket) or DeviceMetric (REST)."""
    return {
        "topic": m.topic,
        "device": m.device,
        "number": m.number,
        "group": m.group,
        "name": m.name,
        "unit": m.unit,
        "platform": m.platform,
        "device_class": m.device_class,
        "state_class": m.state_class,
        "unit_of_measurement": m.unit_of_measurement,
        "min": m.min,
        "max": m.max,
        "options": m.options,
        "payload_on": m.payload_on,
        "payload_off": m.payload_off,
    }
