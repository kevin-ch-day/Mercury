"""Read-only disaster recovery status hub."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mercury.backup.freshness import FRESHNESS_STALE, backup_entry_status_label
from mercury.backup.status import BackupStatusEntry, BackupStatusReport, build_backup_status_report
from mercury.core.usb_mount import resolve_usb_mount
from mercury.core.runtime import should_probe_database_status
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.state.ledger import read_operator_database_backup_rows
from mercury.terminal import screen as display_screen
from mercury.terminal.format import format_human_datetime
from mercury.terminal.table import Table, TableStyle

RECOVERY_SCREEN_TITLE = "Disaster Recovery"


@dataclass(frozen=True)
class RecoveryScreenData:
    report: BackupStatusReport
    restore_check_status: dict[str, str]
    latest_transfer_runbook: Path | None
    latest_database_runbook: Path | None


def read_recovery_choice() -> str | None:
    return read_submenu_choice()


def _status_label(entry: BackupStatusEntry) -> str:
    return backup_entry_status_label(entry)


def _latest_restore_check_status() -> dict[str, str]:
    latest: dict[str, tuple[str, str]] = {}
    for row in read_operator_database_backup_rows():
        database = (row.get("database") or "").strip()
        status = (row.get("restore_check_status") or "").strip()
        stamp = (row.get("timestamp") or "").strip()
        if not database or not status:
            continue
        existing = latest.get(database)
        if existing is None or stamp >= existing[0]:
            latest[database] = (stamp, status)
    return {database: status for database, (_stamp, status) in latest.items()}


def _latest_runbook(pattern: str) -> Path | None:
    usb_mount = resolve_usb_mount()
    root = usb_mount / "mercury_runbooks"
    if not root.is_dir():
        return None
    matches = sorted(root.glob(pattern))
    return matches[-1] if matches else None


def _load_recovery_screen() -> RecoveryScreenData:
    report = build_backup_status_report(live=should_probe_database_status())
    return RecoveryScreenData(
        report=report,
        restore_check_status=_latest_restore_check_status(),
        latest_transfer_runbook=_latest_runbook("transfer_runbook_*.md"),
        latest_database_runbook=_latest_runbook("*/database_transfer_runbook_*.md"),
    )


def _restorecheck_label(status: str | None) -> str:
    if status == "passed":
        return "Passed"
    if status == "failed":
        return "Failed"
    return "Unknown"


def _latest_verified_backup(report: BackupStatusReport) -> str:
    timestamps = [
        entry.backup_created_at
        for entry in report.entries
        if entry.protection_status == "verified" and entry.backup_created_at
    ]
    if not timestamps:
        return "-"
    latest = max(timestamps)
    return format_human_datetime(latest)


def _recovery_table_rows(data: RecoveryScreenData) -> list[list[str]]:
    rows: list[list[str]] = []
    for entry in data.report.entries:
        rows.append(
            [
                entry.database,
                _status_label(entry),
                format_human_datetime(entry.backup_created_at),
                _restorecheck_label(data.restore_check_status.get(entry.database)),
            ]
        )
    return rows


def _risk_summary(report: BackupStatusReport) -> str:
    parts: list[str] = []
    if report.stale_count:
        parts.append(f"{report.stale_count} stale")
    unknown_sources = max(0, report.unknown_freshness_count)
    if unknown_sources:
        parts.append(f"{unknown_sources} unknown")
    if report.missing_count:
        parts.append(f"{report.missing_count} missing")
    if report.failed_count:
        parts.append(f"{report.failed_count} unverified")
    if not parts:
        return "Recovery baseline complete for protected sources."
    return "Protection gaps: " + "; ".join(parts) + "."


def _activity_warning_lines(report: BackupStatusReport) -> list[str]:
    warnings: list[str] = []
    for entry in report.entries:
        if (
            entry.freshness == FRESHNESS_STALE
            and entry.backup_created_at
            and entry.latest_source_activity_at
        ):
            warnings.append(
                f"{entry.database}: latest source activity {format_human_datetime(entry.latest_source_activity_at)} "
                f"is newer than verified backup {format_human_datetime(entry.backup_created_at)}."
            )
    return warnings


def _render_recovery_screen(data: RecoveryScreenData, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(RECOVERY_SCREEN_TITLE)
    report = data.report
    display_screen.write_fields(
        {
            "Protected sources": report.source_count,
            "Verified backups": report.verified_count,
            "Latest safe backup": _latest_verified_backup(report),
            "Recovery runbooks": str(resolve_usb_mount() / "mercury_runbooks"),
        }
    )
    display_screen.write_blank()
    table = Table.from_headers(
        ["DATABASE", "STATUS", "LAST VERIFIED", "RESTORE-CHECK"],
        _recovery_table_rows(data),
        style=TableStyle(indent=0),
        min_col_widths=[30, 10, 24, 14],
        max_col_widths=[32, 12, 28, 14],
    )
    display_screen.write_structured_table(table)
    display_screen.write_blank()
    display_screen.write_summary(_risk_summary(report))
    for warning in _activity_warning_lines(report):
        display_screen.write_hint(warning)
    if data.latest_transfer_runbook:
        display_screen.write_hint(f"Latest transfer runbook: {data.latest_transfer_runbook}")
    elif data.latest_database_runbook:
        display_screen.write_hint(f"Latest database runbook: {data.latest_database_runbook}")
    display_screen.write_hint("Emergency next step: use Backup Operations for stale sources, then use restore-check or System Deployment as needed.")
    display_screen.write_blank()
    render_submenu([("1", "Refresh")], indent=0)


def run_recovery_menu(*, interactive: bool = True) -> None:
    data = _load_recovery_screen()
    show_title = True
    while True:
        _render_recovery_screen(data, show_title=show_title)
        show_title = False
        if not interactive:
            return

        choice = read_recovery_choice()
        if choice in {None, "0"}:
            return
        if choice == "1":
            data = _load_recovery_screen()
            display_screen.write_summary(
                f"Refreshed — {data.report.verified_count} verified, {data.report.missing_count} missing."
            )
            show_title = pause_and_redraw()
            continue
        menu_display.write_status("fail", menu_prompts.invalid_choice_message(choice))
