"""Runtime mode and operator status for menu/CLI."""

from mercury.core.execution_policy import load_execution_policy
from mercury.core.paths import LOCAL_CONFIG
from mercury.core.safety import MODE_SEED

try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment]


def _backup_root_from_config() -> str | None:
    policy = load_execution_policy()
    root = policy.backup_root
    if root and str(root).strip():
        return str(root)
    return None


def _database_reachable() -> bool:
    from mercury.database.mariadb.errors import MariaDbLiveError
    from mercury.database.mariadb.session import readonly_scalar, try_load_mariadb_config

    cfg = try_load_mariadb_config()
    if cfg is None:
        return False
    try:
        readonly_scalar(cfg, "SELECT 1 AS ok")
        return True
    except (MariaDbLiveError, OSError):
        return False


def should_probe_database_status() -> bool:
    """True when MariaDB config is present and a live probe is worthwhile."""
    from mercury.database.mariadb.session import try_load_mariadb_config

    return try_load_mariadb_config() is not None


def operator_status(*, database_connected: bool | None = None, probe_database: bool = False) -> dict[str, str]:
    """Status lines for menu and env probe."""
    policy = load_execution_policy()
    backup_root = _backup_root_from_config()

    if policy.dry_run or not policy.live_actions_enabled:
        safety = "dry-run only"
        mode = f"{MODE_SEED} / dry-run"
    else:
        safety = "live actions enabled"
        mode = "operational"

    if database_connected is None and probe_database:
        database_connected = _database_reachable()
    elif database_connected is None:
        database_connected = False

    db_label = "connected" if database_connected else "not connected"
    if database_connected and LOCAL_CONFIG.exists():
        cfg = None
        try:
            from mercury.database.mariadb.session import try_load_mariadb_config

            cfg = try_load_mariadb_config()
        except Exception:
            cfg = None
        if cfg and cfg.use_client and cfg.unix_socket:
            db_label = f"connected (client:{cfg.unix_socket})"

    return {
        "mode": mode,
        "database": db_label,
        "backup_root": backup_root if backup_root else "not configured",
        "safety": safety,
    }
