"""WebSocket client for SolarAssistant.

Basic usage (local password)::

    sock = await connect(Options(local_ip="192.168.1.100", password="secret"))
    await sock.subscribe_metrics(lambda m: print(m.topic, m.value, m.unit))
    await sock.listen()  # blocks until disconnected

Cloud usage (API key → authorize → connect)::

    async with SolarAssistantClient(api_key) as client:
        auth = await authorize_site(client, site_id)
    sock = await connect(Options(
        host=auth.host,
        local_ip=auth.local_ip,
        token=auth.token,
        site_id=auth.site_id,
        site_key=auth.site_key,
    ))
    await sock.subscribe_metrics(handler)
    await sock.listen()
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
LOCAL_CONNECT_TIMEOUT = 0.5
CLOUD_CONNECT_TIMEOUT = 5.0


@dataclass
class TopicFilter:
    topic: str
    max_frequency_s: int = 0


@dataclass
class Metric:
    """A single metric value pushed by the SolarAssistant WebSocket.

    Discovery fields (``platform``, ``device_class``, ``state_class``,
    ``unit_of_measurement``, ``min``, ``max``, ``options``,
    ``payload_on``, ``payload_off``) come from the ``definition`` event
    and are populated for every metric on builds that ship the enriched
    discovery payload (SA backend update of 2026-05-07). On older builds
    they remain ``None``.
    """
    topic: str
    device: str
    number: int | None
    group: str
    name: str
    value: Any
    unit: str
    platform: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit_of_measurement: str | None = None
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None
    payload_on: str | None = None
    payload_off: str | None = None


@dataclass
class Message:
    """A raw Phoenix Channel message: ``[join_ref, ref, topic, event, payload]``."""
    join_ref: str
    ref: str
    topic: str
    event: str
    payload: dict[str, Any]


@dataclass
class Options:
    """Configuration for a SolarAssistant WebSocket connection.

    For direct local connections (no cloud account):
        Set ``local_ip`` and ``password``. host/token/site_id/site_key are unused.

    For cloud connections:
        Set ``host``, ``token``, ``site_id``, ``site_key`` (all from AuthorizeResponse).
        Optionally set ``local_ip`` to try local network first and fall back to cloud.
    """
    host: str = ""
    local_ip: str = ""
    token: str = ""
    password: str = ""
    site_id: int = 0
    site_key: str = ""
    verbose: bool = False


MetricHandler = Callable[[Metric], None]
MessageHandler = Callable[[Message], None | Awaitable[None]]


@dataclass
class _Subscription:
    topic: str
    event: str
    fn: MessageHandler


class Socket:
    """A connected SolarAssistant WebSocket."""

    def __init__(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        session: aiohttp.ClientSession,
        connected_host: str,
        verbose: bool = False,
    ) -> None:
        self._ws = ws
        self._session = session
        self._verbose = verbose
        self._ref = 0
        self._subs: list[_Subscription] = []
        self._defs: dict[str, dict[str, Any]] = {}
        self.connected_host = connected_host

    # ------------------------------------------------------------------
    # Public API (mirrors Go: SubscribeMetrics, Subscribe, Join, JoinWithPayload, Listen, Close)

    async def subscribe_metrics(
        self,
        handler: MetricHandler,
        *filters: TopicFilter,
    ) -> None:
        """Register a metric handler and join the metrics channel.

        ``definition`` events arrive once and populate metric metadata;
        ``data`` events arrive continuously and invoke ``handler``.

        Pass ``TopicFilter`` instances to subscribe to specific topics
        server-side; otherwise the server applies its default curated set
        (``total/*``, selected ``battery_*/*`` and ``inverter_*/*`` topics).
        Only metrics in groups Info, Status, and Settings are sent.
        """
        self.subscribe("metrics", "definition", self._on_definition)
        self.subscribe("metrics", "data", lambda msg: self._on_data(msg, handler))

        payload: dict[str, Any] = {}
        if filters:
            payload["topics"] = [
                {
                    "topic": f.topic,
                    **({"max_frequency_s": f.max_frequency_s} if f.max_frequency_s else {}),
                }
                for f in filters
            ]
        await self.join_with_payload("metrics", payload)

    def subscribe(self, topic: str, event: str, fn: MessageHandler) -> None:
        """Register a handler for a specific topic+event.

        ``"*"`` is a wildcard for either field. For metrics use
        :meth:`subscribe_metrics` instead.
        """
        self._subs.append(_Subscription(topic, event, fn))

    async def set_setting(self, topic: str, value: str) -> None:
        """Write a setting via the metrics channel.

        Joins the metrics channel, sends a ``set`` message for the given
        MQTT-style topic, and waits for the ``set_result`` reply.

        Args:
            topic: MQTT-style topic, e.g. ``"inverter_1/power_mode"``.
            value: New value as a string.

        Raises:
            ValueError: If the server rejects the setting.
        """
        join_ref = self._next_ref()
        await self._send(join_ref, self._next_ref(), "metrics", "phx_join", {})

        # Wait for join ack then set_result
        async for raw in self._ws:
            frame = json.loads(raw.data)
            _, _, ch_topic, event, payload = frame
            if ch_topic == "metrics" and event == "phx_reply" and payload.get("status") == "ok":
                break

        await self._send(join_ref, self._next_ref(), "metrics", "set", {"topic": topic, "value": value})

        async for raw in self._ws:
            frame = json.loads(raw.data)
            _, _, _, event, payload = frame
            if event == "set_result" and payload.get("topic") == topic:
                if payload.get("result") != "ok":
                    raise ValueError(payload.get("message", "unknown error"))
                return

    async def join(self, topic: str) -> None:
        """Send a ``phx_join`` for the given channel topic."""
        await self.join_with_payload(topic, {})

    async def join_with_payload(self, topic: str, payload: dict[str, Any]) -> None:
        """Send a ``phx_join`` with a custom payload."""
        join_ref = self._next_ref()
        await self._send(join_ref, self._next_ref(), topic, "phx_join", payload)

    async def listen(self) -> None:
        """Read messages in a loop, dispatching to subscribers.

        Also starts the heartbeat. Blocks until the connection closes.
        """
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._dispatch_raw(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            heartbeat.cancel()

    async def close(self) -> None:
        """Close the WebSocket and underlying session."""
        await self._ws.close()
        await self._session.close()

    # ------------------------------------------------------------------
    # Internal

    def _on_definition(self, msg: Message) -> None:
        for defn in msg.payload.get("definitions", []):
            t = defn.get("topic")
            if t:
                self._defs[t] = defn

    def _on_data(self, msg: Message, handler: MetricHandler) -> None:
        for item in msg.payload.get("metrics", []):
            t = item.get("topic", "")
            defn = self._defs.get(t, {})
            handler(
                Metric(
                    topic=t,
                    device=defn.get("device", "") or "",
                    number=defn.get("number"),
                    group=defn.get("group", "") or "",
                    name=defn.get("name", "") or "",
                    value=item.get("value"),
                    unit=defn.get("unit", "") or "",
                    platform=defn.get("platform"),
                    device_class=defn.get("device_class"),
                    state_class=defn.get("state_class"),
                    unit_of_measurement=defn.get("unit_of_measurement"),
                    min=defn.get("min"),
                    max=defn.get("max"),
                    options=defn.get("options"),
                    payload_on=defn.get("payload_on"),
                    payload_off=defn.get("payload_off"),
                )
            )

    async def _heartbeat(self) -> None:
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._send("", self._next_ref(), "phoenix", "heartbeat", {})
            except asyncio.CancelledError:
                return
            except Exception:
                return

    async def _dispatch_raw(self, raw: str) -> None:
        if self._verbose:
            print(f"< recv {raw}", file=sys.stderr)
        msg = _decode(raw)
        if msg is None:
            return
        for sub in list(self._subs):
            if (sub.topic == "*" or sub.topic == msg.topic) and (
                sub.event == "*" or sub.event == msg.event
            ):
                result = sub.fn(msg)
                if asyncio.iscoroutine(result):
                    await result

    async def _send(
        self, join_ref: str, ref: str, topic: str, event: str, payload: Any
    ) -> None:
        data = json.dumps([join_ref, ref, topic, event, payload])
        if self._verbose:
            print(f"> send {data}", file=sys.stderr)
        await self._ws.send_str(data)

    def _next_ref(self) -> str:
        self._ref += 1
        return str(self._ref)


def _decode(raw: str) -> Message | None:
    try:
        frame = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(frame, list) or len(frame) < 5:
        return None
    join_ref, ref, topic, event, payload = frame[0], frame[1], frame[2], frame[3], frame[4]
    if not isinstance(payload, dict):
        payload = {}
    return Message(
        join_ref=_str(join_ref),
        ref=_str(ref),
        topic=_str(topic),
        event=_str(event),
        payload=payload,
    )


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


# ----------------------------------------------------------------------
# Connect


class ConnectError(Exception):
    """Raised when a WebSocket connection cannot be established."""


async def connect(opts: Options) -> Socket:
    """Dial a SolarAssistant WebSocket and return a ready Socket.

    If ``local_ip`` is set, tries the local address first (500 ms timeout)
    and falls back to the cloud proxy ``host`` on failure. The cloud JWT
    works for local connections too.
    """
    session = aiohttp.ClientSession()

    if opts.local_ip:
        credential = opts.password if opts.password else opts.token
        is_password = bool(opts.password)
        try:
            ws = await _dial(
                session, "ws", opts.local_ip,
                credential, is_password, 0, "",
                LOCAL_CONNECT_TIMEOUT, opts.verbose,
            )
            return Socket(ws, session, opts.local_ip, opts.verbose)
        except Exception as exc:
            if not opts.host:
                await session.close()
                raise ConnectError(f"could not connect to {opts.local_ip}: {exc}") from exc
            _LOGGER.debug("Local connection to %s failed (%s), trying cloud", opts.local_ip, exc)

    if not opts.host:
        await session.close()
        raise ConnectError("No reachable host: provide host or local_ip")

    try:
        ws = await _dial(
            session, "wss", opts.host,
            opts.token, False, opts.site_id, opts.site_key,
            CLOUD_CONNECT_TIMEOUT, opts.verbose,
        )
        return Socket(ws, session, opts.host, opts.verbose)
    except Exception:
        await session.close()
        raise


async def _dial(
    session: aiohttp.ClientSession,
    scheme: str,
    host: str,
    credential: str,
    is_password: bool,
    site_id: int,
    site_key: str,
    timeout: float,
    verbose: bool,
) -> aiohttp.ClientWebSocketResponse:
    params = {"vsn": "2.0.0", ("password" if is_password else "token"): credential}
    headers: dict[str, str] = {}
    if not is_password:
        if site_id:
            headers["site-id"] = str(site_id)
        if site_key:
            headers["site-key"] = site_key

    url = f"{scheme}://{host}/api/websocket"
    if verbose:
        print(f"> WS {url} params={params} headers={headers}", file=sys.stderr)

    try:
        return await session.ws_connect(
            url,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(connect=timeout, total=None),
        )
    except aiohttp.WSServerHandshakeError as e:
        msg = _handshake_error_message(e.status)
        raise ConnectError(msg) from e
    except asyncio.TimeoutError as e:
        raise ConnectError("connection timed out — is the device reachable?") from e
    except aiohttp.ClientConnectorError as e:
        raise ConnectError(f"connection failed: {e}") from e


def _handshake_error_message(status: int) -> str:
    if status in (401, 403):
        return f"authentication failed (HTTP {status}) — check your token"
    if status == 404:
        return (
            "WebSocket endpoint not found (HTTP 404) — site may be running an "
            "outdated version (requires build 2026-03-24 or later)"
        )
    if status in (502, 503, 504):
        return f"site is offline or unreachable (HTTP {status})"
    return f"connection rejected (HTTP {status})"
