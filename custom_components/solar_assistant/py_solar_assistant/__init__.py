"""Python client for SolarAssistant — cloud API and real-time WebSocket."""
from .api.v1.sites import (
    AuthorizeResponse,
    Site,
    SiteOwner,
    authorize_site,
    list_sites,
)
from .client import (
    DEFAULT_BASE_URL,
    DeviceMetric,
    SolarAssistantClient,
    SolarAssistantError,
    get_device_metrics,
    set_metric,
)
from .socket import (
    ConnectError,
    Message,
    Metric,
    Options,
    Socket,
    TopicFilter,
    connect,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Cloud API
    "DEFAULT_BASE_URL",
    "SolarAssistantClient",
    "SolarAssistantError",
    "Site",
    "SiteOwner",
    "AuthorizeResponse",
    "list_sites",
    "authorize_site",
    # Device REST
    "DeviceMetric",
    "get_device_metrics",
    "set_metric",
    # WebSocket
    "Options",
    "Socket",
    "Metric",
    "Message",
    "TopicFilter",
    "ConnectError",
    "connect",
]
