"""Read-only inspection of a single database on the server."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.database.core import classify_database, exclusion_reason
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.session import (
    MariaDbLiveError,
    fetch_user_database_names,
)


def _sql_escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


class DatabaseInspectResult(BaseModel):
    name: str
    exists_on_server: bool = False
    role: str
    backup_source: bool
    table_count: int | None = None
    view_count: int | None = None
    total_bytes: int | None = None
    connected: bool = False
    access_mode: str = "unknown"
    notes: list[str] = Field(default_factory=list)
    error: str | None = None


def inspect_database_on_server(
    name: str,
    config: MariaDbConnectionConfig,
    *,
    names_fn=None,
    scalar_fn=None,
) -> DatabaseInspectResult:
    """
    Read-only inspect: confirm database exists and gather table/view/size stats.

    Uses information_schema only — no writes.
    """
    classification = classify_database(name)
    base = DatabaseInspectResult(
        name=name,
        role=classification.role.value,
        backup_source=classification.backup_source,
        access_mode="client" if config.use_client else "pymysql",
    )

    if not classification.backup_source and not classification.dev_target:
        reason = exclusion_reason(classification) or "Not a recognized platform database."
        base.notes.append(reason)

    fetch_names = names_fn or fetch_user_database_names
    fetch_scalar = scalar_fn

    try:
        server_names = fetch_names(config)
    except MariaDbLiveError as exc:
        base.error = str(exc)
        return base

    if name not in server_names:
        base.notes.append("Database not found on server (SHOW DATABASES).")
        return base

    base.exists_on_server = True
    base.connected = True

    if fetch_scalar is None:
        from mercury.database.mariadb.session import readonly_scalar

        fetch_scalar = readonly_scalar

    escaped = _sql_escape_literal(name)
    try:
        table_count = int(
            fetch_scalar(
                config,
                "SELECT COUNT(*) FROM information_schema.tables "
                f"WHERE table_schema = '{escaped}' AND table_type = 'BASE TABLE'",
            )
            or "0"
        )
        view_count = int(
            fetch_scalar(
                config,
                "SELECT COUNT(*) FROM information_schema.tables "
                f"WHERE table_schema = '{escaped}' AND table_type = 'VIEW'",
            )
            or "0"
        )
        total_bytes = int(
            fetch_scalar(
                config,
                "SELECT COALESCE(SUM(data_length + index_length), 0) "
                "FROM information_schema.tables "
                f"WHERE table_schema = '{escaped}'",
            )
            or "0"
        )
    except (MariaDbLiveError, ValueError) as exc:
        base.error = str(exc)
        return base

    base.table_count = table_count
    base.view_count = view_count
    base.total_bytes = total_bytes
    base.notes.append("Read-only inspection via information_schema.")
    return base
