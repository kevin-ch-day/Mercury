"""Configuration status (seed: example files only)."""

from mercury.database.core import configured_database_names
from mercury.paths import DATABASES_LOCAL, LOCAL_CONFIG


def config_status() -> dict[str, str]:
    """Report whether optional local config files exist."""
    from mercury.database import discover_from_config

    inventory = discover_from_config()
    return {
        "databases.toml": (
            "present" if DATABASES_LOCAL.exists() else "not configured (use databases.example.toml)"
        ),
        "local.toml": (
            "present" if LOCAL_CONFIG.exists() else "not configured (use local.example.toml)"
        ),
        "known_databases": str(inventory.count),
        "connection": inventory.connection,
        "primary_config": inventory.primary_config or "platform/catalog only",
        "backup_root": "not configured",
        "mode": "seed",
        "dry_run": "true",
    }


__all__ = ["config_status", "configured_database_names"]
