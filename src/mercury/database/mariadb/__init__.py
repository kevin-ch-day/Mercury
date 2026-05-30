"""MariaDB connectivity and live read-only discovery."""

from mercury.database.mariadb.config import (
    DEFAULT_PASSWORD_ENV,
    MariaDbConfigError,
    MariaDbConnectionConfig,
    load_mariadb_config,
)
from mercury.database.mariadb.live import (
    MariaDbDriverMissingError,
    MariaDbLiveError,
    READ_ONLY_SQL,
    SYSTEM_DATABASES,
    discover_databases_live,
    fetch_database_names,
)
from mercury.database.mariadb.probe import (
    ReadOnlyDiscoveryPlan,
    ToolingProbe,
    build_readonly_discovery_plan,
    probe_client_tooling,
)

__all__ = [
    "DEFAULT_PASSWORD_ENV",
    "MariaDbConfigError",
    "MariaDbConnectionConfig",
    "load_mariadb_config",
    "MariaDbLiveError",
    "MariaDbDriverMissingError",
    "discover_databases_live",
    "fetch_database_names",
    "READ_ONLY_SQL",
    "SYSTEM_DATABASES",
    "SYSTEM_DATABASES",
    "ReadOnlyDiscoveryPlan",
    "ToolingProbe",
    "build_readonly_discovery_plan",
    "probe_client_tooling",
]
