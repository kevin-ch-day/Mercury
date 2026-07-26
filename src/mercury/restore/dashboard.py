"""Consolidated Restore and Disaster Recovery dashboard model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mercury.backup.freshness import (
    backup_entry_artifact_label,
    backup_entry_freshness_label,
    backup_entry_needs_restore_check,
    backup_entry_verify_label,
)
from mercury.backup.status import (
    BackupStatusEntry,
    BackupStatusReport,
    build_recovery_scope_status_report,
    sealed_phase3b_package_note,
)
from mercury.core.runtime import should_probe_database_status
from mercury.core.usb_mount import resolve_operator_mount
from mercury.restore.check_plan import RestoreCheckPlan, build_restore_check_plan
from mercury.restore.recovery_scope import (
    REQUIRED_RECOVERY_DATABASES,
    REQUIRED_RECOVERY_DEVELOPMENT,
    REQUIRED_RECOVERY_PRODUCTION,
    is_required_recovery_production,
)
from mercury.terminal.format import format_human_datetime


@dataclass(frozen=True)
class RecoveryDashboardRow:
    database: str
    role: str
    freshness: str
    artifact: str
    restore_check: str
    last_backup: str
    backup_id: str | None
    pending: bool
    runnable: bool


@dataclass(frozen=True)
class RecoveryDashboard:
    report: BackupStatusReport
    rows: list[RecoveryDashboardRow]
    readiness: str
    production_backed_up: int
    production_total: int
    development_backed_up: int
    development_total: int
    restore_checks_passed: int
    restore_checks_pending: int
    pending_names: list[str]
    runnable_pending: list[str]
    temp_restore_schemas: list[str]
    latest_backup_label: str
    package_line: str
    runbooks_path: str
    plans_by_database: dict[str, RestoreCheckPlan] = field(default_factory=dict)


def _restore_check_display(entry: BackupStatusEntry | None) -> tuple[str, bool]:
    if entry is None:
        return "Missing", True
    label = backup_entry_verify_label(entry)
    if label == "Restore-check passed":
        return "Passed", False
    if label == "Restore-check failed":
        return "Failed", True
    if label in {"Not restore-checked", "OK* · no RC"}:
        return "PENDING", True
    if label in {"Missing", "Absent"}:
        return "—", True
    if "unstamped" in label.lower():
        return "PENDING", True
    return label, backup_entry_needs_restore_check(entry)


def _backed_up(entry: BackupStatusEntry | None) -> bool:
    if entry is None:
        return False
    return entry.protection_status == "verified" and bool(entry.backup_id)


def _latest_backup_label(report: BackupStatusReport) -> str:
    timestamps = [
        entry.backup_created_at
        for entry in report.entries
        if entry.protection_status == "verified" and entry.backup_created_at
    ]
    if not timestamps:
        return "none"
    return format_human_datetime(max(timestamps))


def build_recovery_dashboard(*, live: bool | None = None) -> RecoveryDashboard:
    """Observe-only recovery dashboard for the seven required databases."""
    probe = should_probe_database_status() if live is None else live
    report = build_recovery_scope_status_report(live=probe)
    by_name = {entry.database: entry for entry in report.entries}

    plans: dict[str, RestoreCheckPlan] = {}
    for name in REQUIRED_RECOVERY_PRODUCTION:
        try:
            plans[name] = build_restore_check_plan(name)
        except Exception:
            continue

    rows: list[RecoveryDashboardRow] = []
    pending_names: list[str] = []
    runnable_pending: list[str] = []
    passed = 0

    for name in REQUIRED_RECOVERY_DATABASES:
        entry = by_name.get(name)
        rc_label, pending = _restore_check_display(entry)
        if rc_label == "Passed":
            passed += 1
        if pending and _backed_up(entry):
            pending_names.append(name)
        runnable = False
        if name in plans and plans[name].allowed and pending and _backed_up(entry):
            runnable = True
            runnable_pending.append(name)
        elif (
            pending
            and _backed_up(entry)
            and not is_required_recovery_production(name)
        ):
            # Development restore-check lane is a tracked gap (A-3-02); show PENDING.
            rc_label = "PENDING"
        rows.append(
            RecoveryDashboardRow(
                database=name,
                role="prod" if is_required_recovery_production(name) else "dev",
                freshness=backup_entry_freshness_label(entry),
                artifact=backup_entry_artifact_label(entry),
                restore_check=rc_label,
                last_backup=format_human_datetime(
                    entry.backup_created_at if entry else None
                ),
                backup_id=entry.backup_id if entry else None,
                pending=pending and _backed_up(entry),
                runnable=runnable,
            )
        )

    prod_backed = sum(
        1
        for name in REQUIRED_RECOVERY_PRODUCTION
        if _backed_up(by_name.get(name))
    )
    dev_backed = sum(
        1
        for name in REQUIRED_RECOVERY_DEVELOPMENT
        if _backed_up(by_name.get(name))
    )
    pending_count = len(pending_names)
    backed_total = sum(
        1 for name in REQUIRED_RECOVERY_DATABASES if _backed_up(by_name.get(name))
    )
    if pending_count:
        readiness = f"NOT READY · {pending_count} restore-checks pending"
    elif backed_total < len(REQUIRED_RECOVERY_DATABASES):
        missing = len(REQUIRED_RECOVERY_DATABASES) - backed_total
        readiness = f"NOT READY · {missing} databases missing verified backups"
    else:
        readiness = "READY · all required restore-checks passed"

    package = sealed_phase3b_package_note() or "No sealed Phase 3B package noted"
    if package.startswith("Phase 3B"):
        package = "Phase 3B sealed"

    temp_schemas: list[str] = []
    try:
        from mercury.restore.check_cleanup import discover_restorecheck_names

        temp_schemas = discover_restorecheck_names()
    except Exception:
        temp_schemas = []

    return RecoveryDashboard(
        report=report,
        rows=rows,
        readiness=readiness,
        production_backed_up=prod_backed,
        production_total=len(REQUIRED_RECOVERY_PRODUCTION),
        development_backed_up=dev_backed,
        development_total=len(REQUIRED_RECOVERY_DEVELOPMENT),
        restore_checks_passed=passed,
        restore_checks_pending=pending_count,
        pending_names=pending_names,
        runnable_pending=runnable_pending,
        temp_restore_schemas=temp_schemas,
        latest_backup_label=_latest_backup_label(report),
        package_line=package,
        runbooks_path=str(resolve_operator_mount() / "mercury_runbooks"),
        plans_by_database=plans,
    )


def pending_restore_check_plans(dashboard: RecoveryDashboard) -> list[RestoreCheckPlan]:
    """Plans for [1] Run pending — only runnable production gaps."""
    return [
        dashboard.plans_by_database[name]
        for name in dashboard.runnable_pending
        if name in dashboard.plans_by_database
    ]


def selected_restore_check_plans(
    dashboard: RecoveryDashboard,
    names: list[str],
) -> list[RestoreCheckPlan]:
    plans: list[RestoreCheckPlan] = []
    for name in names:
        plan = dashboard.plans_by_database.get(name)
        if plan is None and is_required_recovery_production(name):
            plan = build_restore_check_plan(name)
        if plan is not None and plan.allowed:
            plans.append(plan)
    return plans
