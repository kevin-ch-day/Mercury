"""Batch read-only database size and table statistics."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.readonly_session import readonly_connection
from mercury.database.mariadb.session import SYSTEM_DATABASES, MariaDbLiveError


class DatabaseStats(BaseModel):
    name: str
    table_count: int = 0
    view_count: int = 0
    total_bytes: int = 0


class DatabaseStatsReport(BaseModel):
    access_mode: str
    databases: list[DatabaseStats] = Field(default_factory=list)
    total_bytes: int = 0
    notes: list[str] = Field(default_factory=list)


def _stats_sql() -> str:
    excluded = ", ".join(f"'{name}'" for name in sorted(SYSTEM_DATABASES))
    return (
        "SELECT table_schema, "
        "COALESCE(SUM(CASE WHEN table_type = 'BASE TABLE' THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN table_type = 'VIEW' THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(data_length + index_length), 0) "
        "FROM information_schema.tables "
        f"WHERE table_schema NOT IN ({excluded}) "
        "GROUP BY table_schema "
        "ORDER BY table_schema"
    )


def fetch_all_database_stats(
    config: MariaDbConnectionConfig,
    *,
    row_fn=None,
) -> DatabaseStatsReport:
    """
    Fetch table/view/byte totals for all user databases in one query.

    Read-only via information_schema only.
    """
    fetch_rows = row_fn or _default_fetch_rows
    access_mode = "client" if config.use_client else "pymysql"
    try:
        rows = fetch_rows(config, _stats_sql())
    except MariaDbLiveError as exc:
        raise exc

    databases: list[DatabaseStats] = []
    total_bytes = 0
    for row in rows:
        if len(row) < 4:
            continue
        name = row[0]
        table_count = int(row[1])
        view_count = int(row[2])
        bytes_val = int(row[3])
        databases.append(
            DatabaseStats(
                name=name,
                table_count=table_count,
                view_count=view_count,
                total_bytes=bytes_val,
            )
        )
        total_bytes += bytes_val

    return DatabaseStatsReport(
        access_mode=access_mode,
        databases=databases,
        total_bytes=total_bytes,
        notes=[
            "Read-only batch stats from information_schema.tables.",
            "Sizes include data and index length only.",
        ],
    )


def _default_fetch_rows(config: MariaDbConnectionConfig, sql: str) -> list[list[str]]:
    with readonly_connection(config) as session:
        return session.rows(sql)
