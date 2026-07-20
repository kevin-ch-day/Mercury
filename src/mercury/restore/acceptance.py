"""Fail-closed, read-only acceptance contracts for future restored databases.

This module plans and runs only explicit read-only checks.  It intentionally
does not create a database, call a routine, compile a view through DDL, or
start an application pipeline.  A separately authorized restore remains a
prerequisite for any live use of these checks.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from pydantic import BaseModel, Field

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.session import readonly_scalars

_READONLY_START = re.compile(r"^(?:SHOW|SELECT|EXPLAIN)\b", re.IGNORECASE)
_FORBIDDEN_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|CALL|"
    r"GRANT|REVOKE|SET|LOAD\s+DATA|INTO\s+OUTFILE|FOR\s+UPDATE|LOCK\s+TABLES)\b",
    re.IGNORECASE,
)
_COMMENTS = re.compile(r"(?:--|/\*|\*/|#)")


class ReadOnlyAcceptanceError(ValueError):
    """Raised when an acceptance statement is not in the strict allowlist."""


class AcceptanceQuery(BaseModel):
    """One named, predeclared acceptance query and its expected scalar value."""

    check_id: str
    description: str
    sql: str
    expected: str | None = None


class AcceptanceResult(BaseModel):
    """Read-only result for one acceptance query."""

    check_id: str
    passed: bool
    observed: list[str] = Field(default_factory=list)
    expected: str | None = None
    detail: str = ""


class AcceptanceReport(BaseModel):
    """Results for a declared acceptance contract; no write behavior exists."""

    queries: list[AcceptanceQuery] = Field(default_factory=list)
    results: list[AcceptanceResult] = Field(default_factory=list)
    passed: bool = False


def assert_readonly_acceptance_sql(sql: str) -> None:
    """Reject everything outside one un-commented SHOW, SELECT, or EXPLAIN."""
    normalized = sql.strip()
    if not normalized or ";" in normalized:
        raise ReadOnlyAcceptanceError("Acceptance SQL must contain one statement without a semicolon.")
    if _COMMENTS.search(normalized):
        raise ReadOnlyAcceptanceError("Acceptance SQL must not contain SQL comments.")
    if not _READONLY_START.match(normalized):
        raise ReadOnlyAcceptanceError("Acceptance SQL must begin with SHOW, SELECT, or EXPLAIN.")
    if _FORBIDDEN_SQL.search(normalized):
        raise ReadOnlyAcceptanceError("Acceptance SQL contains a write-capable or locking keyword.")


def validate_acceptance_contract(queries: Iterable[AcceptanceQuery]) -> list[AcceptanceQuery]:
    """Validate all statements before opening any database connection."""
    declared = list(queries)
    seen: set[str] = set()
    for query in declared:
        if query.check_id in seen:
            raise ReadOnlyAcceptanceError(f"Duplicate acceptance check id: {query.check_id}")
        seen.add(query.check_id)
        assert_readonly_acceptance_sql(query.sql)
    return declared


def run_readonly_acceptance(
    config: MariaDbConnectionConfig,
    queries: Iterable[AcceptanceQuery],
    *,
    scalars: Callable[[MariaDbConnectionConfig, str], list[str]] = readonly_scalars,
) -> AcceptanceReport:
    """Run a prevalidated read-only contract through the standard read-only path."""
    declared = validate_acceptance_contract(queries)
    results: list[AcceptanceResult] = []
    for query in declared:
        observed = [str(value) for value in scalars(config, query.sql)]
        passed = query.expected is None or query.expected in observed
        results.append(
            AcceptanceResult(
                check_id=query.check_id,
                passed=passed,
                observed=observed,
                expected=query.expected,
                detail="matched expected value" if passed else "expected value was not returned",
            )
        )
    return AcceptanceReport(
        queries=declared,
        results=results,
        passed=all(result.passed for result in results),
    )


def phase2a_readonly_acceptance_contract() -> list[AcceptanceQuery]:
    """Return the immutable object-count baseline for the future desktop check."""
    return [
        AcceptanceQuery(
            check_id="erebus_objects",
            description="Erebus base-table and view count",
            sql=(
                "SELECT CONCAT(SUM(TABLE_TYPE = 'BASE TABLE'), ':', SUM(TABLE_TYPE = 'VIEW')) "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'erebus_threat_intel_prod'"
            ),
            expected="125:76",
        ),
        AcceptanceQuery(
            check_id="erebus_triggers",
            description="Erebus trigger count",
            sql=(
                "SELECT COUNT(*) FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA = 'erebus_threat_intel_prod'"
            ),
            expected="15",
        ),
        AcceptanceQuery(
            check_id="erebus_procedures",
            description="Erebus stored-procedure count",
            sql=(
                "SELECT COUNT(*) FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA = 'erebus_threat_intel_prod' AND ROUTINE_TYPE = 'PROCEDURE'"
            ),
            expected="7",
        ),
        AcceptanceQuery(
            check_id="erebus_procedure_names",
            description="Exact Erebus stored-procedure names",
            sql=(
                "SELECT GROUP_CONCAT(ROUTINE_NAME ORDER BY ROUTINE_NAME SEPARATOR ',') "
                "FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = 'erebus_threat_intel_prod' "
                "AND ROUTINE_TYPE = 'PROCEDURE'"
            ),
            expected=(
                "erebus_apply_0221_android_family_lamda_gap_v2,"
                "refresh_sample_permission_actionable_unknown_cache,"
                "sp_permission_prefix_policy_set,sp_permission_triage_override_disable,"
                "sp_permission_triage_override_set,sp_permission_triage_repair_seen_count,"
                "sp_refresh_vt_sample_verdict_confidence_current"
            ),
        ),
        AcceptanceQuery(
            check_id="erebus_functions_events",
            description="Erebus function and event counts",
            sql=(
                "SELECT CONCAT((SELECT COUNT(*) FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA = 'erebus_threat_intel_prod' AND ROUTINE_TYPE = 'FUNCTION'), ':', "
                "(SELECT COUNT(*) FROM information_schema.EVENTS "
                "WHERE EVENT_SCHEMA = 'erebus_threat_intel_prod'))"
            ),
            expected="0:0",
        ),
        AcceptanceQuery(
            check_id="permission_objects",
            description="Permission Intel base-table and view count",
            sql=(
                "SELECT CONCAT(SUM(TABLE_TYPE = 'BASE TABLE'), ':', SUM(TABLE_TYPE = 'VIEW')) "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'android_permission_intel'"
            ),
            expected="41:35",
        ),
        AcceptanceQuery(
            check_id="permission_triggers",
            description="Permission Intel trigger count",
            sql=(
                "SELECT COUNT(*) FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA = 'android_permission_intel'"
            ),
            expected="24",
        ),
        AcceptanceQuery(
            check_id="permission_routines_events",
            description="Permission Intel routine and event counts",
            sql=(
                "SELECT CONCAT((SELECT COUNT(*) FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA = 'android_permission_intel'), ':', "
                "(SELECT COUNT(*) FROM information_schema.EVENTS "
                "WHERE EVENT_SCHEMA = 'android_permission_intel'))"
            ),
            expected="0:0",
        ),
        AcceptanceQuery(
            check_id="obsidiandroid_core_objects",
            description="ObsidianDroid core base-table and view baseline",
            sql=(
                "SELECT CONCAT(SUM(TABLE_TYPE = 'BASE TABLE'), ':', SUM(TABLE_TYPE = 'VIEW')) "
                "FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'obsidiandroid_core_prod'"
            ),
            expected="7:0",
        ),
    ]
