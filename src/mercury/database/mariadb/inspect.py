"""Read-only inspection of a single database on the server."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.database.core import classify_database, exclusion_reason
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.session import MariaDbLiveError, readonly_row


def _sql_escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _inspect_sql(schema: str) -> str:
    return (
        "SELECT "
        "(SELECT COUNT(*) FROM information_schema.schemata "
        f"WHERE schema_name = '{schema}'), "
        "COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN table_type = 'VIEW' THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(data_length + index_length), 0) "
        "FROM information_schema.tables "
        f"WHERE table_schema = '{schema}'"
    )


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
    row_fn=None,
) -> DatabaseInspectResult:
    """
    Read-only inspect: confirm database exists and gather table/view/size stats.

    Uses information_schema only — no writes. Single combined query.
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

    fetch_row = row_fn or readonly_row

    escaped = _sql_escape_literal(name)
    try:
        inspect_row = fetch_row(config, _inspect_sql(escaped))
        if not inspect_row or len(inspect_row) < 4:
            raise ValueError("Unexpected inspect row")
        exists_count = int(inspect_row[0])
        table_count = int(inspect_row[1])
        view_count = int(inspect_row[2])
        total_bytes = int(inspect_row[3])
    except (MariaDbLiveError, ValueError) as exc:
        base.error = str(exc)
        return base

    if exists_count == 0:
        base.notes.append("Database not found on server (information_schema.schemata).")
        return base

    base.exists_on_server = True
    base.connected = True
    base.table_count = table_count
    base.view_count = view_count
    base.total_bytes = total_bytes
    base.notes.append("Read-only inspection via information_schema.")
    return base
