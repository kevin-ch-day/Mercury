"""Main menu dashboard — live status snapshot for the Mercury home screen."""

from __future__ import annotations

from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.terminal.format import short_path
from mercury.terminal.theme import dashboard_row
from mercury.core.runtime import operator_status


def dashboard_rows(*, probe_database: bool | None = None) -> list[str]:
    """Status lines shown under ``Main menu`` on the home screen."""
    probe = should_probe_database_status() if probe_database is None else probe_database
    status = operator_status(probe_database=probe)
    connected = "connected" in status["database"].lower() and "not connected" not in status["database"].lower()
    mode_tag = "[--]" if "dry-run" in status["safety"] else "[ok]"
    db_tag = "[ok]" if connected else "[!!]"

    policy = load_execution_policy()
    backup_root = status["backup_root"]
    if backup_root != "not configured":
        backup_path = short_path(backup_root, max_len=44)
    else:
        backup_path = "not configured"

    rows = [
        dashboard_row("Mode", f"{mode_tag} {status['safety']}"),
        dashboard_row("Database connection", db_tag),
        dashboard_row("Backups location", backup_path),
    ]

    coverage = _backup_coverage_summary(live=probe and connected)
    if coverage is not None:
        rows.append(dashboard_row("Backup coverage", coverage))

    backup_count = _count_on_disk_backups(policy.backup_root)
    if backup_count is not None:
        label = "backup" if backup_count == 1 else "backups"
        rows.append(dashboard_row("On disk", f"{backup_count} {label}"))

    sync_summary = _sync_readiness_summary(live=probe and connected)
    if sync_summary is not None:
        ready, blocked = sync_summary
        rows.append(dashboard_row("Prod→dev sync", f"{ready} ready · {blocked} blocked"))
        if ready == 0 and blocked > 0 and "dry-run" in status["safety"]:
            rows.append(dashboard_row("Next step", "[3] Backup → [5] Verify → [6] Sync"))

    return rows


def _backup_coverage_summary(*, live: bool) -> str | None:
    try:
        from mercury.backup.batch_runner import resolve_batch_sources
        from mercury.backup.find_latest_backup import find_latest_backup_directory

        sources = resolve_batch_sources(live=live)
        if not sources:
            return None
        policy = load_execution_policy()
        on_disk = sum(
            1
            for name in sources
            if find_latest_backup_directory(policy.backup_root, name) is not None
        )
        return f"{on_disk}/{len(sources)} prod sources"
    except Exception:
        return None


def _count_on_disk_backups(backup_root) -> int | None:
    try:
        from mercury.backup.on_disk_index import build_on_disk_backup_list

        listing = build_on_disk_backup_list(backup_root)
        return len(listing.records)
    except OSError:
        return None


def _sync_readiness_summary(*, live: bool) -> tuple[int, int] | None:
    try:
        from mercury.sync.readiness import build_sync_readiness_report

        report = build_sync_readiness_report(live=live)
        return report.ready_count, report.blocked_count
    except Exception:
        return None
