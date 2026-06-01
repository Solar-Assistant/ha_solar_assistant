"""HTTP client for the SolarAssistant cloud API."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import aiohttp

DEFAULT_BASE_URL = "https://solar-assistant.io"
_TIMEOUT = aiohttp.ClientTimeout(total=10)

_PAGINATION_KEYS = ("limit", "offset")
_DEVICE_REST_USERNAME = "admin"


@dataclass
class DeviceMetric:
    """One row from ``GET /api/v1/metrics`` on a SolarAssistant unit.

    Discovery fields (``platform``, ``device_class``, ``state_class``,
    ``unit_of_measurement``, ``min``, ``max``, ``options``,
    ``payload_on``, ``payload_off``) are populated only when the request
    used ``?discovery``; otherwise they're ``None``.
    """
    topic: str
    name: str
    unit: str
    value: Any
    group: str
    device: str
    number: int | None
    platform: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit_of_measurement: str | None = None
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None
    payload_on: str | None = None
    payload_off: str | None = None


class SolarAssistantClient:
    """Authenticated client for the SolarAssistant cloud API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        verbose: bool = False,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.verbose = verbose
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "SolarAssistantClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        """GET <path> with optional filter params.

        Pagination keys (limit, offset) are sent as top-level query params.
        All other keys are joined as key:value and sent as a single ?q= param,
        which is the standard filter mechanism across all v1 list endpoints.
        Pass None or an empty dict to fetch all records.
        """
        q: dict[str, str] = {}
        filters: list[str] = []
        for k, v in (params or {}).items():
            if k in _PAGINATION_KEYS:
                q[k] = str(v)
            else:
                filters.append(f"{k}:{v}")
        if filters:
            q["q"] = " ".join(filters)
        return await self._do("GET", self._base_url + path, q)

    async def post(self, path: str) -> bytes:
        """POST <path> with no request body."""
        return await self._do("POST", self._base_url + path, {})

    async def _do(self, method: str, url: str, params: dict[str, str]) -> bytes:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        session = self._session or aiohttp.ClientSession()
        owned = self._session is None

        if self.verbose:
            print(f"> {method} {url} {params}", file=sys.stderr)

        try:
            async with session.request(
                method, url, params=params, headers=headers, timeout=_TIMEOUT
            ) as resp:
                body = await resp.read()
                if self.verbose:
                    print(f"< {resp.status} {body.decode(errors='replace').strip()}", file=sys.stderr)
                if resp.status != 200:
                    raise SolarAssistantError(resp.status, body.decode(errors="replace"))
                return body
        finally:
            if owned:
                await session.close()


class SolarAssistantError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"API error {status}: {message.strip()}")
        self.status = status


async def get_device_metrics(
    host: str,
    *,
    password: str | None = None,
    token: str | None = None,
    discovery: bool = True,
    topic: str | None = None,
    scheme: str = "http",
    timeout: float = 10.0,
) -> list[DeviceMetric]:
    """Fetch ``GET /api/v1/metrics`` from a SolarAssistant unit.

    Auth: pass ``password`` for local HTTP Basic (``admin:<web-password>``),
    or ``token`` for a Bearer-style JWT (works for both local and cloud
    proxy hosts).

    Set ``discovery=True`` (default) to request the HA-discovery superset
    (``platform``, ``device_class``, ``min``/``max``/``options``/etc.).
    Set ``topic="inverter_1/foo"`` to filter the response to a single
    metric.
    """
    if not password and not token:
        raise ValueError("get_device_metrics requires password or token")

    params: list[str] = []
    if discovery:
        params.append("discovery")
    if topic:
        from urllib.parse import quote
        params.append(f"topic={quote(topic, safe='')}")
    query = ("?" + "&".join(params)) if params else ""

    url = f"{scheme}://{host}/api/v1/metrics{query}"
    auth = aiohttp.BasicAuth(_DEVICE_REST_USERNAME, password) if password else None
    headers = {"Authorization": f"Bearer {token}"} if token and not password else {}

    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            auth=auth,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.read()
            if resp.status != 200:
                raise SolarAssistantError(resp.status, body.decode(errors="replace"))
            import json
            rows = json.loads(body)

    return [_row_to_metric(r) for r in rows]


async def set_metric(
    host: str,
    topic: str,
    value: str,
    *,
    password: str | None = None,
    token: str | None = None,
    scheme: str = "http",
    timeout: float = 10.0,
    site_id: int = 0,
    site_key: str = "",
) -> None:
    """Write a setting via ``POST /api/v1/metrics``.

    Args:
        host: IP address or hostname of the SolarAssistant device.
        topic: MQTT-style topic, e.g. ``"inverter_1/power_mode"``.
        value: New value as a string.
        site_id: Required for cloud-proxy connections.
        site_key: Required for cloud-proxy connections.

    Raises:
        SolarAssistantError: If the server returns an error.
    """
    if not password and not token:
        raise ValueError("set_metric requires password or token")

    import json as _json
    url = f"{scheme}://{host}/api/v1/metrics"
    auth = aiohttp.BasicAuth(_DEVICE_REST_USERNAME, password) if password else None
    headers: dict[str, str] = {}
    if token and not password:
        headers["Authorization"] = f"Bearer {token}"
        if site_id:
            headers["site-id"] = str(site_id)
        if site_key:
            headers["site-key"] = site_key

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json={"topic": topic, "value": value},
            auth=auth,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.read()
            if resp.status != 200:
                try:
                    msg = _json.loads(body).get("error", body.decode(errors="replace"))
                except Exception:
                    msg = body.decode(errors="replace")
                raise SolarAssistantError(resp.status, msg)


def _row_to_metric(r: dict[str, Any]) -> DeviceMetric:
    return DeviceMetric(
        topic=r.get("topic", "") or "",
        name=r.get("name", "") or "",
        unit=r.get("unit", "") or "",
        value=r.get("value"),
        group=r.get("group", "") or "",
        device=r.get("device", "") or "",
        number=r.get("number"),
        platform=r.get("platform"),
        device_class=r.get("device_class"),
        state_class=r.get("state_class"),
        unit_of_measurement=r.get("unit_of_measurement"),
        min=r.get("min"),
        max=r.get("max"),
        options=r.get("options"),
        payload_on=r.get("payload_on"),
        payload_off=r.get("payload_off"),
    )
