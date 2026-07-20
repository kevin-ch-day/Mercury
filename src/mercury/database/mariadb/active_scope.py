"""Read-only active-scope database snapshot for the current Mercury milestone."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.database.core import DatabaseRole, classify_database
from mercury.database.core.scope import (
    ACTIVE_BACKUP_SOURCE_DATABASES,
    ACTIVE_DEV_RECOVERY_DATABASES,
    ACTIVE_DEV_TARGET_DATABASES,
    is_active_backup_source,
    is_active_dev_target,
    is_active_sync_source,
)
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.readonly_session import readonly_connection
from mercury.terminal.format import format_bytes


class ActiveScopeDatabaseRow(BaseModel):
    name: str
    role: str
    exists_on_server: bool
    backup_source: bool
    sync_role: str
    table_count: int = 0
    view_count: int = 0
    total_bytes: int = 0

    @property
    def size_label(self) -> str:
        return format_bytes(self.total_bytes)

    @property
    def status_label(self) -> str:
        return "present" if self.exists_on_server else "missing"


class ActiveScopeReport(BaseModel):
    access_mode: str
    database_count: int
    present_count: int
    missing_count: int
    total_bytes: int = 0
    rows: list[ActiveScopeDatabaseRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _active_scope_names() -> list[str]:
    return sorted(ACTIVE_BACKUP_SOURCE_DATABASES | ACTIVE_DEV_RECOVERY_DATABASES)


def _sql_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _scope_sql() -> str:
    selects = " UNION ALL ".join(
        f"SELECT '{_sql_literal(name)}' AS scope_name" for name in _active_scope_names()
    )
    return (
        "SELECT scope.scope_name, "
        "MAX(CASE WHEN schemata.schema_name IS NOT NULL THEN 1 ELSE 0 END), "
        "COALESCE(SUM(CASE WHEN tables.table_type = 'BASE TABLE' THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN tables.table_type = 'VIEW' THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(tables.data_length + tables.index_length), 0) "
        f"FROM ({selects}) AS scope "
        "LEFT JOIN information_schema.schemata AS schemata "
        "ON schemata.schema_name = scope.scope_name "
        "LEFT JOIN information_schema.tables AS tables "
        "ON tables.table_schema = scope.scope_name "
        "GROUP BY scope.scope_name "
        "ORDER BY scope.scope_name"
    )


def fetch_active_scope_report(
    config: MariaDbConnectionConfig,
    *,
    rows_fn=None,
) -> ActiveScopeReport:
    """Fetch active-scope presence and size stats in one read-only query."""
    fetch_rows = rows_fn or _default_fetch_rows
    rows = fetch_rows(config, _scope_sql())
    report_rows: list[ActiveScopeDatabaseRow] = []
    total_bytes = 0
    present = 0
    missing = 0
    for row in rows:
        if len(row) < 5:
            continue
        name = row[0]
        exists_on_server = str(row[1]) not in {"0", "", "False", "false"}
        table_count = int(row[2])
        view_count = int(row[3])
        total = int(row[4])
        classification = classify_database(name)
        if classification.role == DatabaseRole.SHARED_AUTHORITY:
            sync_role = "backup-only"
        elif is_active_sync_source(name):
            sync_role = "source+pair"
        elif classification.backup_source and is_active_backup_source(name):
            sync_role = "backup-only"
        elif is_active_dev_target(name):
            sync_role = "dev target"
        else:
            sync_role = "manual review"
        report_rows.append(
            ActiveScopeDatabaseRow(
                name=name,
                role=classification.role.value,
                exists_on_server=exists_on_server,
                backup_source=classification.backup_source,
                sync_role=sync_role,
                table_count=table_count,
                view_count=view_count,
                total_bytes=total,
            )
        )
        total_bytes += total
        if exists_on_server:
            present += 1
        else:
            missing += 1

    return ActiveScopeReport(
        access_mode="client" if config.use_client else "pymysql",
        database_count=len(report_rows),
        present_count=present,
        missing_count=missing,
        total_bytes=total_bytes,
        rows=report_rows,
        notes=[
            "Read-only active-scope snapshot from information_schema.",
            "Covers active source databases and active dev targets only.",
        ],
    )


def _default_fetch_rows(config: MariaDbConnectionConfig, sql: str) -> list[list[str]]:
    with readonly_connection(config) as session:
        return session.rows(sql)
