"""Config flow for SolarAssistant — local password or cloud API key."""
from __future__ import annotations

import fnmatch
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

# Inner field name used inside every section. Its label is rendered as
# "Add metric" via translations, regardless of which section it lives in.
_SECTION_FIELD = "topics"

from py_solar_assistant import (
    ConnectError,
    DeviceMetric,
    Options,
    SolarAssistantClient,
    SolarAssistantError,
    authorize_site,
    connect,
    get_device_metrics,
    get_device_site_id,
    list_sites,
)
from .const import (
    AUTH_CLOUD,
    AUTH_LOCAL,
    CONF_API_KEY,
    CONF_AUTH_METHOD,
    CONF_ENABLED_TOPICS,
    CONF_HOST,
    CONF_LOCAL_IP,
    CONF_PASSWORD,
    CONF_SITE_ID,
    CONF_SITE_KEY,
    CONF_SITE_NAME,
    CONF_TOKEN,
    DEFAULT_LOCAL_HOST,
    DOMAIN,
    MDNS_SCAN_TIMEOUT_S,
    MDNS_SERVICE_TYPE,
)

_LOGGER = logging.getLogger(__name__)
_SITE_SEARCH_FIELD = "search"
_SITE_SEARCH_LIMIT = 50


class SolarAssistantConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._sites: list[Any] = []
        self._search: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SolarAssistantOptionsFlow(config_entry)

    # -- Entry point: pick auth method --

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=["local", "cloud"],
        )

    # -- Path A: local password --

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip().rstrip("/")
            password = user_input[CONF_PASSWORD]
            try:
                sock = await connect(Options(local_ip=host, password=password))
                await sock.close()
            except ConnectError as err:
                _LOGGER.debug("Local connect failed: %s", err)
                msg = str(err)
                errors["base"] = (
                    "invalid_auth" if ("401" in msg or "403" in msg) else "cannot_connect"
                )
            except Exception:
                _LOGGER.exception("Unexpected error verifying local connection")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"local:{host}")
                self._abort_if_unique_id_configured()
                data: dict[str, Any] = {
                    CONF_AUTH_METHOD: AUTH_LOCAL,
                    CONF_HOST: host,
                    CONF_PASSWORD: password,
                }
                try:
                    from homeassistant.components.zeroconf import async_get_instance
                    zc = await async_get_instance(self.hass)
                    site_id = await self.hass.async_add_executor_job(
                        _mdns_site_id_sync, zc, host
                    )
                except Exception:
                    site_id = None
                if site_id is None:
                    try:
                        site_id = await get_device_site_id(host, password=password)
                    except SolarAssistantError as err:
                        _LOGGER.debug("Failed to get site_id over REST: %s", err)
                if site_id is not None:
                    data[CONF_SITE_ID] = site_id
                return self.async_create_entry(
                    title=f"SolarAssistant ({host})",
                    data=data,
                )

        default_host = (
            DEFAULT_LOCAL_HOST
            if not self.hass.config_entries.async_entries(DOMAIN)
            else ""
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=default_host): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="local", data_schema=schema, errors=errors)

    # -- Path B: cloud API key --

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = user_input[CONF_API_KEY].strip()
            self._sites, err = await self._list_sites()
            if err:
                errors["base"] = err
            elif not self._sites:
                errors["base"] = "no_sites"
            elif len(self._sites) >= _SITE_SEARCH_LIMIT:
                return await self.async_step_search_site()
            else:
                return await self.async_step_pick_site()

        schema = vol.Schema({vol.Required(CONF_API_KEY): str})
        return self.async_show_form(
            step_id="cloud", data_schema=schema, errors=errors
        )

    async def async_step_pick_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Few sites were found, pick a site directly"""
        errors: dict[str, str] = {}

        if user_input is not None:
            picked = user_input.get(CONF_SITE_ID)
            if picked:
                result = await self._create_cloud_entry(int(picked))
                if isinstance(result, str):
                    errors["base"] = result
                else:
                    return result

        options = self._site_options()
        if not options:
            return self.async_abort(reason="all_sites_configured")

        return self.async_show_form(
            step_id="pick_site",
            data_schema=vol.Schema(self._site_field(options, required=True)),
            errors=errors,
        )

    async def async_step_search_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Many sites were found, search for a site then pick a match.

        Re-entrant: a changed term re-runs the search; an unchanged term plus a
        pick adds it.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            submitted = (user_input.get(_SITE_SEARCH_FIELD) or "").strip()
            picked = user_input.get(CONF_SITE_ID)
            if picked and submitted == self._search:
                result = await self._create_cloud_entry(int(picked))
                if isinstance(result, str):
                    errors["base"] = result
                else:
                    return result
            else:
                self._search = submitted
                if self._search:
                    self._sites, err = await self._list_sites(search=self._search)
                    if err:
                        errors["base"] = err
                else:
                    self._sites = []

        options = self._site_options() if self._search else []
        if self._search and not errors and not options:
            errors["base"] = "no_sites_match"

        fields: dict[Any, Any] = {
            vol.Optional(_SITE_SEARCH_FIELD, default=self._search): str,
        }
        fields.update(self._site_field(options, required=False))
        return self.async_show_form(
            step_id="search_site", data_schema=vol.Schema(fields), errors=errors
        )

    async def _list_sites(self, **filters: Any) -> tuple[list[Any], str | None]:
        """List sites, optionally with a free-text ``search=`` term (a
        prefix/full-text match).

        Returns (sites, error_key).
        """
        try:
            async with SolarAssistantClient(self._api_key) as client:
                sites = await list_sites(client, limit=_SITE_SEARCH_LIMIT, **filters)
            return sites, None
        except SolarAssistantError as err:
            _LOGGER.debug("Site query failed: %s", err)
            return [], "invalid_auth" if err.status in (401, 403) else "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error querying sites")
            return [], "unknown"

    def _site_field(self, options: list[Any], *, required: bool) -> dict[Any, Any]:
        """Build the site-dropdown schema field, pre-selecting a lone match."""
        if not options:
            return {}
        key_cls = vol.Required if required else vol.Optional
        key = (
            key_cls(CONF_SITE_ID, default=options[0]["value"])
            if len(options) == 1
            else key_cls(CONF_SITE_ID)
        )
        return {
            key: selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options, mode=selector.SelectSelectorMode.DROPDOWN
                )
            )
        }

    def _site_options(self) -> list[Any]:
        """Dropdown options from the current sites, hiding configured ones."""
        existing = {
            entry.unique_id for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        choices = {
            str(s.id): f"{s.name} ({s.inverter or 'unknown'})"
            for s in self._sites
            if f"cloud:{s.id}" not in existing
        }
        return [
            selector.SelectOptionDict(value=value, label=label)
            for value, label in sorted(choices.items(), key=lambda kv: kv[1].lower())
        ]

    async def _create_cloud_entry(self, site_id: int) -> ConfigFlowResult | str:
        """Authorize a site and create its entry; return the result or an error key."""
        site = next((s for s in self._sites if s.id == site_id), None)
        if site is None:
            return "unknown"
        try:
            async with SolarAssistantClient(self._api_key) as client:
                auth = await authorize_site(client, site_id)
        except SolarAssistantError as err:
            _LOGGER.debug("authorize_site failed: %s", err)
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error authorizing site")
            return "unknown"
        await self.async_set_unique_id(f"cloud:{site_id}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"SolarAssistant - {auth.site_name or site.name}",
            data={
                CONF_AUTH_METHOD: AUTH_CLOUD,
                CONF_API_KEY: self._api_key,
                CONF_SITE_ID: auth.site_id,
                CONF_SITE_NAME: auth.site_name or site.name,
                CONF_SITE_KEY: auth.site_key,
                CONF_TOKEN: auth.token,
                CONF_HOST: auth.host,
                CONF_LOCAL_IP: auth.local_ip,
            },
        )


class SolarAssistantOptionsFlow(OptionsFlow):
    """Pick which metrics become Home Assistant entities.

    Discovery via ``GET /api/v1/metrics`` runs each time the form opens,
    so new device categories or metric groups added by future Solar
    Assistant releases automatically appear as additional sections —
    no integration code change required.

    Selections are stored as glob patterns (e.g. ``inverter_*/load_power``).
    Picking a glob enables the metric across every instance of that
    device (inverter_1, inverter_2, …), so users don't have to tick
    each instance individually.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            metrics = await self._discover_metrics()
        except SolarAssistantError as err:
            _LOGGER.warning("Discovery via /api/v1/metrics failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        except Exception:
            _LOGGER.exception("Unexpected error discovering metrics")
            return self.async_abort(reason="unknown")

        form_sections = _build_sections(metrics)
        if not form_sections:
            return self.async_abort(reason="no_metrics")

        if user_input is not None:
            selected: list[str] = []
            for sec in form_sections.values():
                inner = user_input.get(sec["key"]) or {}
                selected.extend(inner.get(_SECTION_FIELD, []) or [])
            return self.async_create_entry(
                title="",
                data={CONF_ENABLED_TOPICS: sorted(set(selected))},
            )

        from .const import DEFAULT_CURATED_GLOBS
        current_globs = self._entry.options.get(CONF_ENABLED_TOPICS)
        if current_globs is None:
            current_globs = list(DEFAULT_CURATED_GLOBS)

        schema_dict: dict[Any, Any] = {}
        for sec in form_sections.values():
            choices = sec["choices"]
            default = [
                g for g in choices
                if any(
                    fnmatch.fnmatchcase(c, g) or fnmatch.fnmatchcase(g, c)
                    for c in current_globs
                )
            ]
            options_list = [
                selector.SelectOptionDict(value=glob, label=label)
                for glob, label in choices.items()
            ]
            inner = vol.Schema(
                {
                    vol.Optional(_SECTION_FIELD, default=default): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options_list,
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            )
            schema_dict[sec["key"]] = section(inner, {"collapsed": True})

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict)
        )

    async def _discover_metrics(self) -> list[DeviceMetric]:
        d = self._entry.data
        method = d.get(CONF_AUTH_METHOD, AUTH_LOCAL)
        if method == AUTH_LOCAL:
            return await get_device_metrics(d[CONF_HOST], password=d[CONF_PASSWORD])
        local_ip = d.get(CONF_LOCAL_IP) or ""
        token = d.get(CONF_TOKEN) or ""
        if local_ip:
            try:
                return await get_device_metrics(local_ip, token=token)
            except Exception as err:
                _LOGGER.debug("Local REST to %s failed (%s) — trying cloud", local_ip, err)
        host = d.get(CONF_HOST) or ""
        return await get_device_metrics(
            host,
            token=token,
            scheme="https",
            site_id=int(d.get(CONF_SITE_ID, 0) or 0),
            site_key=d.get(CONF_SITE_KEY, ""),
        )


# Group ordering used for non-totals sections. Groups not in this list are
# appended in alphabetical order so a future "Diagnostics" group still appears.
_GROUP_ORDER = ("Status", "Settings", "Info")
_TOTALS_DEVICE = "totals"


def _build_sections(
    metrics: list[DeviceMetric],
) -> dict[Any, dict[str, Any]]:
    """Build the form sections from discovered metrics.

    Totals get one combined section (Status/Settings/Info merged), placed
    first. Every other device renders one section per group. Returns an
    ordered dict; each value has:
      ``key``     — schema field key (e.g. ``"totals"``, ``"inverters_status"``)
      ``label``   — friendly heading
      ``choices`` — ``{glob_pattern: friendly_label}``
    """
    buckets: dict[tuple[str, str], list[DeviceMetric]] = {}
    for m in metrics:
        if m.group == "Ignore" or not m.device:
            continue
        buckets.setdefault((m.device, m.group), []).append(m)

    sections: dict[Any, dict[str, Any]] = {}

    totals_items: list[DeviceMetric] = []
    for (device, _group), items in list(buckets.items()):
        if device == _TOTALS_DEVICE:
            totals_items.extend(items)
    if totals_items:
        sections[_TOTALS_DEVICE] = _make_section(
            key="totals",
            label="Totals",
            items=totals_items,
        )

    def group_rank(g: str) -> tuple[int, str]:
        return (_GROUP_ORDER.index(g) if g in _GROUP_ORDER else len(_GROUP_ORDER), g)

    other_keys = sorted(
        (k for k in buckets if k[0] != _TOTALS_DEVICE),
        key=lambda x: (x[0], group_rank(x[1])),
    )
    for device, group in other_keys:
        sections[(device, group)] = _make_section(
            key=f"{device}_{group.lower()}",
            label=f"{device.title()} · {group}",
            items=buckets[(device, group)],
        )
    return sections


def _make_section(*, key: str, label: str, items: list[DeviceMetric]) -> dict[str, Any]:
    prefix_template = _prefix_template(items[0].topic)
    choices: dict[str, str] = {}
    for m in items:
        sub = m.topic.split("/", 1)[1] if "/" in m.topic else m.topic
        glob = f"{prefix_template}{sub}"
        unit = f" ({m.unit})" if m.unit else ""
        choices[glob] = f"{m.name}{unit}"
    choices = dict(sorted(choices.items(), key=lambda kv: kv[1].lower()))
    return {"key": key, "label": label, "choices": choices}


def _prefix_template(topic: str) -> str:
    """``inverter_1/load_power`` → ``inverter_*/`` ; ``total/pv_power`` → ``total/``.

    Detects per-instance prefixes by the trailing ``_<digits>`` shape, so a
    future ``weather_2/foo`` would correctly become ``weather_*/foo`` without
    any device-specific knowledge.
    """
    prefix = topic.split("/", 1)[0]
    if "_" in prefix:
        stem, last = prefix.rsplit("_", 1)
        if last.isdigit():
            return f"{stem}_*/"
    return f"{prefix}/"


def _mdns_site_id_sync(zc: Any, host: str) -> int | None:
    """Blocking: resolve host → IP, browse mDNS using an existing Zeroconf instance."""
    import socket as _socket
    import time
    from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange

    try:
        target_ip = _socket.gethostbyname(host)
    except OSError:
        return None

    found: list[int] = []

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
        svc_ips = [_socket.inet_ntoa(addr) for addr in (info.addresses or [])]
        if target_ip in svc_ips:
            try:
                found.append(int(props["site_id"]))
            except (KeyError, ValueError):
                pass

    browser = ServiceBrowser(zc, MDNS_SERVICE_TYPE, handlers=[on_change])
    time.sleep(MDNS_SCAN_TIMEOUT_S)
    browser.cancel()
    return found[0] if found else None
