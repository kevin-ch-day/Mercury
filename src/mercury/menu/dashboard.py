"""Main menu dashboard — operator-focused status snapshot."""

from __future__ import annotations

from mercury.core.execution_policy import load_execution_policy
from mercury.core.storage_status import (
    backup_root_summary_label,
    backup_root_storage_status_label,
)
from mercury.core.runtime import should_probe_database_status
from mercury.terminal.theme import body_label, dashboard_row
from mercury.core.runtime import operator_status


def dashboard_rows(*, probe_database: bool | None = None) -> list[str]:
    """Sectioned operator status for the Mercury home screen."""
    probe = should_probe_database_status() if probe_database is None else probe_database
    status = operator_status(probe_database=probe)
    connected = "connected" in status["database"].lower() and "not connected" not in status["database"].lower()
    policy = load_execution_policy()

    rows: list[str] = [
        body_label("Status"),
        dashboard_row("MariaDB", "[ok] connected" if connected else "[!!] unavailable"),
        dashboard_row("Execution mode", "LIVE" if policy.live_execution_allowed() else "DRY RUN"),
        dashboard_row("Backup target", backup_root_summary_label(policy)),
    ]

    if policy.backup_root_state() != "usb-mounted":
        rows.append(dashboard_row("Storage status", backup_root_storage_status_label(policy, styled=True)))

    verified_count, source_total = _verified_source_summary(live=probe and connected)
    ready, blocked, blocker = _sync_readiness_summary(live=probe and connected)

    rows.extend(
        [
            dashboard_row("Source DBs", f"{verified_count} of {source_total} verified"),
            dashboard_row("Sync pairs", f"{ready} ready, {blocked} blocked"),
            dashboard_row("Blocker", blocker),
        ]
    )
    return rows


def _verified_source_summary(*, live: bool) -> tuple[int, int]:
    try:
        from mercury.backup.batch_runner import resolve_batch_sources
        from mercury.backup.find_latest_backup import find_latest_backup_directory
        from mercury.backup.verification import verify_backup_artifacts
        from mercury.core.safety import BACKUP_KIND_FULL

        policy = load_execution_policy()
        sources = resolve_batch_sources(live=live)
        verified = 0
        for name in sources:
            backup_dir = find_latest_backup_directory(policy.backup_root, name)
            if backup_dir is None:
                continue
            if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
                continue
            result = verify_backup_artifacts(backup_dir, database=name)
            if result.verified and result.backup_kind == BACKUP_KIND_FULL:
                verified += 1
        return verified, len(sources)
    except Exception:
        return 0, 0


def _sync_readiness_summary(*, live: bool) -> tuple[int, int, str]:
    try:
        from mercury.sync.readiness import build_sync_readiness_report

        report = build_sync_readiness_report(live=live)
        blocker = "None."
        blocker_messages: list[str] = []
        for entry in report.entries:
            blocker_messages.extend(entry.blockers)
        if blocker_messages:
            if any("No on-disk backup found for production source." in msg for msg in blocker_messages):
                blocker = "No verified full backups exist yet."
            else:
                blocker = blocker_messages[0]
        return report.ready_count, report.blocked_count, blocker
    except Exception:
        return 0, 0, "Readiness status unavailable."
