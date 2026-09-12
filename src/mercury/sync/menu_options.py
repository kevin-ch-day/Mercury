"""Sync submenu option definitions (symbolic IDs; keys are report-dependent)."""

from __future__ import annotations

from mercury.core.execution_policy import load_execution_policy
from mercury.sync.readiness import SyncReadinessReport

ACTION_RECHECK = "recheck"
ACTION_PREPARE = "prepare"
ACTION_SYNC_ALL_READY = "sync_all_ready"
ACTION_SYNC_ONE = "sync_one"
ACTION_VERIFY_DEV = "verify_dev"


def _blocked_prod_sources(report: SyncReadinessReport) -> list[str]:
    return [
        entry.prod
        for entry in report.entries
        if not entry.ready_for_sync_planning and entry.dev_listed
    ]


def _ready_entries(report: SyncReadinessReport):
    return [entry for entry in report.entries if entry.ready_for_sync_planning]


def sync_submenu_options(
    report: SyncReadinessReport, *, live_allowed: bool | None = None
) -> list[tuple[str, str, str]]:
    """Return ``(key, label, action_id)`` for the current sync readiness report."""
    allowed = (
        load_execution_policy().live_execution_allowed()
        if live_allowed is None
        else live_allowed
    )
    options: list[tuple[str, str, str]] = [
        ("1", "Recheck Database Sync Status", ACTION_RECHECK)
    ]
    blocked = _blocked_prod_sources(report)
    ready = _ready_entries(report)
    if blocked:
        label = "Prepare production backups"
        if not allowed:
            label = f"{label} (preview only)"
        options.append(("2", label, ACTION_PREPARE))
    if ready:
        sync_label = "Sync All Ready Databases" if allowed else "Preview All Ready Databases"
        sync_key = "2" if not blocked else "3"
        if report.blocked_count and allowed:
            sync_label = f"{sync_label} (ready pairs only)"
        options.append((sync_key, sync_label, ACTION_SYNC_ALL_READY))
        if report.ready_count > 1:
            single_label = "Sync One Ready Pair" if allowed else "Preview One Ready Pair"
            single_key = "3" if not blocked else "4"
            options.append((single_key, single_label, ACTION_SYNC_ONE))
    verify_key = "4" if not blocked else "5"
    options.append(
        (verify_key, "Verify Dev Targets Against Prod Backups", ACTION_VERIFY_DEV)
    )
    return options


def sync_submenu_hint(
    action_id: str,
    report: SyncReadinessReport,
    *,
    live_allowed: bool,
) -> str:
    for key, label, action in sync_submenu_options(report, live_allowed=live_allowed):
        if action == action_id:
            clean = label.split(" (ready pairs only)", 1)[0]
            clean = clean.split(" (preview only)", 1)[0]
            return f"{clean} [{key}]"
    raise KeyError(f"Unknown or unavailable sync menu action: {action_id}")
