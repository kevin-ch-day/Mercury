"""MariaDB connectivity and live read-only discovery."""

from mercury.database.mariadb.config import (
    DEFAULT_PASSWORD_ENV,
    MariaDbConfigError,
    MariaDbConnectionConfig,
    load_mariadb_config,
)
from mercury.database.mariadb.live import (
    READ_ONLY_SQL,
    discover_databases_live,
)
from mercury.database.mariadb.session import (
    SYSTEM_DATABASES,
    MariaDbDriverMissingError,
    MariaDbLiveError,
    MariaDbServerProbe,
    connect_mariadb,
    fetch_user_database_names,
    probe_mariadb_server,
    readonly_scalar,
    readonly_scalars,
    resolve_mariadb_target,
    try_load_mariadb_config,
)

# Backward-compatible alias
fetch_database_names = fetch_user_database_names
from mercury.database.mariadb.access import PlatformAccessReport, build_platform_access_report
from mercury.database.mariadb.inspect import DatabaseInspectResult, inspect_database_on_server
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
    "fetch_user_database_names",
    "readonly_scalar",
    "readonly_scalars",
    "inspect_database_on_server",
    "DatabaseInspectResult",
    "build_platform_access_report",
    "PlatformAccessReport",
    "READ_ONLY_SQL",
    "SYSTEM_DATABASES",
    "MariaDbServerProbe",
    "connect_mariadb",
    "probe_mariadb_server",
    "resolve_mariadb_target",
    "try_load_mariadb_config",
    "ReadOnlyDiscoveryPlan",
    "ToolingProbe",
    "build_readonly_discovery_plan",
    "probe_client_tooling",
]
