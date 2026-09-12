"""Post-restore isolation: approved dev targets must not keep production schema refs."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Iterable

from pydantic import BaseModel, Field

PRODUCTION_ISOLATION_SCHEMAS: tuple[str, ...] = (
    "android_permission_intel",
    "erebus_threat_intel_prod",
    "scytaledroid_core_prod",
    "obsidiandroid_core_prod",
)
DEV_PERMISSION_INTEL_SCHEMA = "android_permission_intel_dev"


class SchemaReferenceHit(BaseModel):
    schema_name: str
    object_kind: str
    object_name: str | None = None


class IsolationReport(BaseModel):
    target: str
    production_schema_references: int = 0
    dev_pi_references: int = 0
    isolation: str = "PASS"
    production_hits: list[SchemaReferenceHit] = Field(default_factory=list)
    dev_pi_hits: list[SchemaReferenceHit] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

    def format_receipt(self) -> str:
        return (
            f"TARGET={self.target}\n"
            f"PRODUCTION_SCHEMA_REFERENCES={self.production_schema_references}\n"
            f"DEV_PI_REFERENCES={self.dev_pi_references}\n"
            f"ISOLATION={self.isolation}"
        )


def _identifier_pattern(schema: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:`{re.escape(schema)}`|{re.escape(schema)})\s*\.",
        re.IGNORECASE,
    )


def _code_sql(sql: str) -> str:
    from mercury.database.mariadb.import_stream import collect_sql_code

    return collect_sql_code(sql)


def find_schema_qualified_hits(
    sql: str,
    schemas: Iterable[str],
    *,
    object_kind: str,
    object_name: str | None,
) -> list[SchemaReferenceHit]:
    code = _code_sql(sql)
    hits: list[SchemaReferenceHit] = []
    for schema in schemas:
        if _identifier_pattern(schema).search(code):
            hits.append(
                SchemaReferenceHit(schema_name=schema, object_kind=object_kind, object_name=object_name)
            )
    return hits


def scan_sql_isolation(
    statements: list[tuple[str, str | None, str]],
    *,
    target: str,
    allowed_production_schemas: frozenset[str] | None = None,
) -> IsolationReport:
    """Scan (object_kind, object_name, sql) tuples for production schema identifiers."""
    allowed = allowed_production_schemas or frozenset()
    production_hits: list[SchemaReferenceHit] = []
    dev_hits: list[SchemaReferenceHit] = []
    for kind, name, sql in statements:
        production_hits.extend(
            hit
            for hit in find_schema_qualified_hits(
                sql, PRODUCTION_ISOLATION_SCHEMAS, object_kind=kind, object_name=name
            )
            if hit.schema_name not in allowed
        )
        dev_hits.extend(
            find_schema_qualified_hits(
                sql, (DEV_PERMISSION_INTEL_SCHEMA,), object_kind=kind, object_name=name
            )
        )
    passed = not production_hits
    issues = [
        f"{hit.object_kind} {hit.object_name or '(unnamed)'} references production schema {hit.schema_name}"
        for hit in production_hits
    ]
    return IsolationReport(
        target=target,
        production_schema_references=len(production_hits),
        dev_pi_references=len(dev_hits),
        isolation="PASS" if passed else "FAIL",
        production_hits=production_hits,
        dev_pi_hits=dev_hits,
        issues=issues,
    )


def inspect_restored_schema_isolation(
    target: str,
    *,
    query_fn: Callable | None = None,
    config=None,
    allowed_production_schemas: frozenset[str] | None = None,
) -> IsolationReport:
    """Read restored object bodies and fail closed on production schema identifiers."""
    fetch = query_fn or _default_rows
    statements: list[tuple[str, str | None, str]] = []
    quoted = target.replace("'", "''")
    view_rows = fetch(
        config,
        "SELECT TABLE_NAME, VIEW_DEFINITION FROM information_schema.VIEWS "
        f"WHERE TABLE_SCHEMA = '{quoted}'",
    )
    for row in _split_rows(view_rows):
        if len(row) >= 2:
            statements.append(("view", row[0], row[1]))
    routine_rows = fetch(
        config,
        "SELECT ROUTINE_NAME, ROUTINE_TYPE, ROUTINE_DEFINITION FROM information_schema.ROUTINES "
        f"WHERE ROUTINE_SCHEMA = '{quoted}'",
    )
    for row in _split_rows(routine_rows):
        if len(row) >= 3:
            statements.append((str(row[1]).lower(), row[0], row[2] or ""))
    trigger_rows = fetch(
        config,
        "SELECT TRIGGER_NAME, ACTION_STATEMENT FROM information_schema.TRIGGERS "
        f"WHERE TRIGGER_SCHEMA = '{quoted}'",
    )
    for row in _split_rows(trigger_rows):
        if len(row) >= 2:
            statements.append(("trigger", row[0], row[1]))
    event_rows = fetch(
        config,
        "SELECT EVENT_NAME, EVENT_DEFINITION FROM information_schema.EVENTS "
        f"WHERE EVENT_SCHEMA = '{quoted}'",
    )
    for row in _split_rows(event_rows):
        if len(row) >= 2:
            statements.append(("event", row[0], row[1]))
    return scan_sql_isolation(
        statements, target=target, allowed_production_schemas=allowed_production_schemas
    )


def _default_rows(config, sql: str) -> list[list[str]]:
    from mercury.database.mariadb.readonly_session import readonly_connection

    with readonly_connection(config) as session:
        return session.rows(sql)


def _split_rows(payload: str | list[list[str]]) -> list[list[str]]:
    if isinstance(payload, list):
        return payload
    rows: list[list[str]] = []
    for line in str(payload).splitlines():
        if line.strip():
            rows.append(line.split("\t"))
    return rows
