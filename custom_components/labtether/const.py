"""Constants for the LabTether integration."""

DOMAIN = "labtether"

CONF_HOST = "host"
CONF_API_KEY = "api_key"
CONF_CA_CERTIFICATE = "ca_certificate"
CONF_NAME = "name"
CONF_IGNORE_CERT_ERRORS = "ignore_cert_errors"
CONF_ALLOW_INSECURE_HTTP = "allow_insecure_http"
CONF_IMPORT_BINARY_SENSORS = "import_status_entities"
CONF_IMPORT_SENSORS = "import_telemetry_sensors"
CONF_IMPORT_SWITCHES = "import_power_switches"
CONF_ENABLE_RUN_ACTION_SERVICE = "enable_run_action_service"
CONF_SCAN_INTERVAL = "scan_interval_seconds"

DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 3600
MAX_ASSETS_PER_RESPONSE = 2048
MAX_ASSET_IDENTITIES_PER_ENTRY = 4096
# Asset IDs count toward the budget above for this long after they were last
# seen, so normal churn (such as recreated Docker containers) ages out.
ASSET_IDENTITY_WINDOW_SECONDS = 24 * 60 * 60
MAX_ASSET_ID_LENGTH = 256
MAX_ASSET_FIELD_LENGTH = 1024
DEFAULT_IMPORT_BINARY_SENSORS = True
DEFAULT_IMPORT_SENSORS = True
DEFAULT_IMPORT_SWITCHES = True
DEFAULT_ENABLE_RUN_ACTION_SERVICE = False
DEFAULT_ALLOW_INSECURE_HTTP = False

# The generic service deliberately exposes only the same bounded power actions
# represented by integration switch entities. Arbitrary connector actions can
# carry destructive parameters and must be initiated through the LabTether UI,
# where the hub can present action-specific authorization and confirmation.
RUN_ACTION_ALLOWLIST = {
    "proxmox": frozenset(
        {"vm.start", "vm.shutdown", "vm.stop", "ct.start", "ct.shutdown", "ct.stop"}
    ),
    "truenas": frozenset({"vm.start", "vm.stop"}),
    "docker": frozenset({"container.start", "container.stop"}),
}

# LabTether API endpoints
API_ASSETS = "/assets"
API_METRICS_OVERVIEW = "/metrics/overview"
API_ALERTS_INSTANCES = "/alerts/instances"
API_ACTIONS_EXECUTE = "/actions/execute"

# HA platforms to set up
PLATFORMS = ["sensor", "binary_sensor", "switch"]

# Asset kinds that support power control (start/stop). The Hub reports Docker
# containers as "docker-container"; "container" covers Proxmox LXC and older Hubs.
CONTROLLABLE_KINDS = {"vm", "container", "docker-container"}
POWER_ACTION_SOURCES = {"proxmox", "truenas", "docker"}
ACTIVE_ASSET_STATUSES = frozenset({"online", "running", "up", "active", "healthy"})
# A device in one of these states sends no telemetry, so its last CPU, memory
# and disk values are old. The LabTether console hides them too.
INACTIVE_ASSET_STATUSES = frozenset({"offline", "stopped", "exited", "down", "dead"})

# Asset source to exclude (prevents circular entity mirroring)
EXCLUDED_SOURCES = frozenset({"home-assistant", "homeassistant"})

CPU_METRIC = "cpu_used_percent"
MEMORY_METRIC = "memory_used_percent"
DISK_METRIC = "disk_used_percent"
_ALL_USAGE_METRICS = frozenset({CPU_METRIC, MEMORY_METRIC, DISK_METRIC})

# The usage metrics the Hub sends for each asset kind. A Docker host sends none
# (the agent on that machine reports them for its host device) and a Docker
# container sends no disk value, so those sensors would stay unknown.
USAGE_METRICS_BY_KIND = {
    "host": _ALL_USAGE_METRICS,
    "hypervisor-node": _ALL_USAGE_METRICS,
    "vm": _ALL_USAGE_METRICS,
    "container": _ALL_USAGE_METRICS,
    "docker-container": frozenset({CPU_METRIC, MEMORY_METRIC}),
}

# Asset kinds that get at least one usage sensor
TELEMETRY_KINDS = frozenset(USAGE_METRICS_BY_KIND)


def usage_metrics(kind) -> frozenset[str]:
    """Return the usage metrics the Hub sends for an asset kind."""
    return USAGE_METRICS_BY_KIND.get(kind, frozenset())


DEFAULT_HUB_DEVICE_NAME = "LabTether Hub"


def hub_device_name(entry) -> str:
    """The Name typed at setup, so two hubs are told apart, else LabTether Hub."""
    return str(entry.data.get(CONF_NAME) or "").strip() or DEFAULT_HUB_DEVICE_NAME


def entry_pref(entry, key: str, default):
    """Return a config entry preference, favoring options over stored data."""
    return entry.options.get(key, entry.data.get(key, default))


def parse_scan_interval(value) -> int | None:
    """Return a valid scan interval, or None for malformed/out-of-range input."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped.isascii() or not stripped.isdecimal():
            return None
        parsed = int(stripped, 10)
    else:
        return None

    if MIN_SCAN_INTERVAL <= parsed <= MAX_SCAN_INTERVAL:
        return parsed
    return None


def scan_interval_or_default(value, default: int = DEFAULT_SCAN_INTERVAL) -> int:
    """Return a safe scan interval, falling back to the integration default."""
    parsed = parse_scan_interval(value)
    if parsed is not None:
        return parsed
    parsed_default = parse_scan_interval(default)
    if parsed_default is not None:
        return parsed_default
    return DEFAULT_SCAN_INTERVAL


def hub_registry_key(entry_id: str) -> str:
    """Return the hub device registry key for a config entry."""
    return f"{entry_id}:hub"


def asset_registry_key(entry_id: str, asset_id: str) -> str:
    """Return the asset device registry key for a config entry."""
    return f"{entry_id}:asset:{asset_id}"
