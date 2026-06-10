"""Live MariaDB inventory helpers for backup safety checks."""

from __future__ import annotations


def fetch_live_server_database_names() -> set[str] | None:
    """Return database names from the live server when MariaDB is configured."""
    from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config

    config = try_load_mariadb_config()
    if config is None:
        return None
    try:
        return set(fetch_user_database_names(config))
    except Exception:
        return None


def live_source_missing_reason(
    database: str,
    *,
    live: bool,
    server_names: set[str] | None,
) -> str | None:
    """Refusal reason when a protected source is absent from live MariaDB."""
    if not live or server_names is None:
        return None
    if database not in server_names:
        return (
            f"Protected source '{database}' is not present on the MariaDB server; "
            "backup refused."
        )
    return None
