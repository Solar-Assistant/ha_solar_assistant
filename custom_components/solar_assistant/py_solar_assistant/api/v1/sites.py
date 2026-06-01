"""SolarAssistant cloud API v1 — sites endpoints.

Endpoint: ``GET /api/v1/sites``

Example filters::

    name:my-site
    inverter:srne
    battery:daly
    inverter_params_output_power:5000
    last_seen_after:2026-01-01
    build_date_after:2026-02-26

Endpoint: ``POST /api/v1/sites/:id/authorize``

Returns a short-lived token for connecting to a site's WebSocket.
The token and ``site_key`` are used when connecting via the cloud.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ...client import SolarAssistantClient

_SITES_PATH = "/api/v1/sites"
_AUTHORIZE_PATH = "/api/v1/sites/{site_id}/authorize"


@dataclass
class SiteOwner:
    email: str = ""


@dataclass
class Site:
    id: int = 0
    name: str = ""
    inverter: str = ""
    inverter_count: int = 0
    inverter_params: dict[str, Any] = field(default_factory=dict)
    battery: str = ""
    battery_count: int = 0
    battery_params: dict[str, Any] = field(default_factory=dict)
    proxy: str = ""
    web_port: Any = None
    ssh_port: Any = None
    arch: str = ""
    build_date: str = ""
    last_seen_at: str = ""
    owner: SiteOwner = field(default_factory=SiteOwner)


@dataclass
class AuthorizeResponse:
    host: str = ""
    site_id: int = 0
    site_name: str = ""
    site_key: str = ""
    token: str = ""
    local_ip: str = ""


async def list_sites(
    client: SolarAssistantClient, **params: Any
) -> list[Site]:
    """Return all sites accessible with the client's API key.

    Keyword arguments are passed as filters (e.g. ``inverter="srne"``,
    ``limit=50``).
    """
    body = await client.get(_SITES_PATH, params or None)
    return [_parse_site(s) for s in json.loads(body)]


async def authorize_site(
    client: SolarAssistantClient, site_id: int
) -> AuthorizeResponse:
    """Return a short-lived token for connecting to a site's WebSocket."""
    body = await client.post(_AUTHORIZE_PATH.format(site_id=site_id))
    r = json.loads(body)
    return AuthorizeResponse(
        host=r.get("host", ""),
        site_id=r.get("site_id", 0),
        site_name=r.get("site_name", ""),
        site_key=r.get("site_key", ""),
        token=r.get("token", ""),
        local_ip=r.get("local_ip", ""),
    )


def _parse_site(s: dict[str, Any]) -> Site:
    return Site(
        id=s.get("id", 0),
        name=s.get("name", ""),
        inverter=s.get("inverter", ""),
        inverter_count=s.get("inverter_count", 0),
        inverter_params=s.get("inverter_params") or {},
        battery=s.get("battery", ""),
        battery_count=s.get("battery_count", 0),
        battery_params=s.get("battery_params") or {},
        proxy=s.get("proxy", ""),
        web_port=s.get("web_port"),
        ssh_port=s.get("ssh_port"),
        arch=s.get("arch", ""),
        build_date=s.get("build_date", ""),
        last_seen_at=s.get("last_seen_at", ""),
        owner=SiteOwner(email=(s.get("owner") or {}).get("email", "")),
    )
