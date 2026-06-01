"""Constants for the SolarAssistant integration."""
DOMAIN = "solar_assistant"

# Config entry data keys
CONF_AUTH_METHOD = "auth_method"
CONF_API_KEY = "api_key"
CONF_HOST = "host"
CONF_LOCAL_IP = "local_ip"
CONF_PASSWORD = "password"
CONF_TOKEN = "token"
CONF_SITE_ID = "site_id"
CONF_SITE_KEY = "site_key"
CONF_SITE_NAME = "site_name"

# Auth method values
AUTH_LOCAL = "local"
AUTH_CLOUD = "cloud"

# Options keys
CONF_ENABLED_TOPICS = "enabled_topics"

# Curated default — same set the SA server uses when no client filter is sent.
# Mirrors @default_topics in solar_assistant_web/channels/metrics.ex.
DEFAULT_CURATED_GLOBS = (
    "total/*",
    "battery_*/voltage",
    "battery_*/state_of_charge",
    "battery_*/power",
    "battery_*/temperature",
    "inverter_*/pv_power",
    "inverter_*/load_power",
    "inverter_*/grid_power",
    "inverter_*/device_mode",
    "inverter_*/temperature",
)

# Default host suggestion (mDNS) for the very first local-password unit
DEFAULT_LOCAL_HOST = "solar-assistant.local"

# Reconnect backoff
RECONNECT_INITIAL_S = 5
RECONNECT_MAX_S = 300

# IP-change recovery
IP_RESCAN_AFTER_S = 300          # start scanning after 5 min of continuous failure
IP_RESCAN_INTERVAL_S = 300       # repeat mDNS scan no more often than every 5 min
CLOUD_IP_REFRESH_INTERVAL_S = 12 * 3600  # re-authorize via cloud API at most once per 12 h

MDNS_SERVICE_TYPE = "_solar-assistant._tcp.local."
MDNS_SCAN_TIMEOUT_S = 5

# Dispatcher signals (per-entry)
def signal_new_metric(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_new_metric"


def signal_metric_update(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_metric_update"


def signal_connection_state(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_connection_state"
