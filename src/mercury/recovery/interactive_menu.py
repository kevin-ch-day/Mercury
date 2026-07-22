"""Read-only disaster recovery status hub."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mercury import output
from mercury.backup.freshness import (
    FRESHNESS_STALE,
    backup_entry_artifact_label,
    backup_entry_freshness_label,
)
from mercury.backup.status import (
    BackupStatusReport,
    build_backup_status_report,
)
from mercury.core.usb_mount import resolve_operator_mount
from mercury.core.runtime import should_probe_database_status
from mercury.handoff.display import handoff_pipeline_line
from mercury.handoff.receiver import build_receiver_handoff_guide
from mercury.handoff.snapshot import build_handoff_snapshot
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.terminal import screen as display_screen
from mercury.terminal.format import format_human_datetime
from mercury.terminal.table import Table, TableStyle
from mercury.terminal.theme import hint_text

RECOVERY_SCREEN_TITLE = "Disaster Recovery"


@dataclass(frozen=True)
class RecoveryScreenData:
    report: BackupStatusReport
    restore_check_status: dict[str, str]
    latest_transfer_runbook: Path | None
    latest_database_runbook: Path | None


def read_recovery_choice() -> str | None:
    return read_submenu_choice()


def _latest_runbook(pattern: str) -> Path | None:
    operator_mount = resolve_operator_mount()
    root = operator_mount / "mercury_runbooks"
    if not root.is_dir():
        return None
    matches = sorted(root.glob(pattern))
    return matches[-1] if matches else None


def _load_recovery_screen() -> RecoveryScreenData:
    report = build_backup_status_report(live=should_probe_database_status())
    # Database-keyed restore-check map is deprecated and unused for display decisions.
    return RecoveryScreenData(
        report=report,
        restore_check_status={},
        latest_transfer_runbook=_latest_runbook("transfer_runbook_*.md"),
        latest_database_runbook=_latest_runbook("*/database_transfer_runbook_*.md"),
    )


def _restorecheck_label(entry) -> str:
    status = getattr(entry, "restore_check_status", None)
    backup_id = getattr(entry, "backup_id", None)
    restore_backup_id = getattr(entry, "restore_check_backup_id", None)
    if not status or not backup_id or restore_backup_id != backup_id:
        return "None"
    if status == "passed":
        return "Passed"
    if status in {"failed", "verification_failed"}:
        return "Failed"
    return "None"


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
                backup_entry_freshness_label(entry),
                backup_entry_artifact_label(entry),
                _restorecheck_label(entry),
                format_human_datetime(entry.backup_created_at),
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
    not_restore_checked = sum(
        1
        for entry in report.entries
        if entry.protection_status == "verified"
        and entry.backup_id
        and (
            entry.restore_check_status is None
            or entry.restore_check_backup_id != entry.backup_id
        )
    )
    restore_failed = sum(
        1
        for entry in report.entries
        if entry.backup_id
        and entry.restore_check_backup_id == entry.backup_id
        and entry.restore_check_status in {"failed", "verification_failed"}
    )
    unstamped = sum(
        1
        for entry in report.entries
        if entry.protection_status == "verified"
        and entry.manifest_verification_stamp is False
    )
    if not_restore_checked:
        parts.append(f"{not_restore_checked} not restore-checked")
    if restore_failed:
        parts.append(f"{restore_failed} restore-check failed")
    if unstamped:
        parts.append(f"{unstamped} unstamped")
    if not parts:
        return "Recovery baseline complete for protected sources."
    if not_restore_checked or restore_failed or unstamped:
        return "Recovery gaps: " + "; ".join(parts) + "."
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


def _write_dense(lines: list[str]) -> None:
    for line in lines:
        output.write(hint_text(line))


def _render_recovery_screen(data: RecoveryScreenData, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(RECOVERY_SCREEN_TITLE)
    report = data.report
    checklist = build_handoff_snapshot(live=should_probe_database_status()).checklist
    display_screen.write_summary(f"Handoff readiness: {checklist.handoff_status}")

    display_screen.write_fields(
        {
            "Pipeline": handoff_pipeline_line(checklist),
            "Sources": f"{report.verified_count}/{report.source_count} verified",
            "Latest verified backup": _latest_verified_backup(report),
            "Latest transfer": checklist.latest_transfer_age or "none",
            "Runbooks": str(resolve_operator_mount() / "mercury_runbooks"),
        }
    )
    table = Table.from_headers(
        ["DATABASE", "FRESH", "ARTIFACT", "RC", "LAST BACKUP"],
        _recovery_table_rows(data),
        style=TableStyle(indent=0, gap=2),
        min_col_widths=[28, 6, 10, 6, 22],
        max_col_widths=[36, 8, 14, 8, 28],
    )
    display_screen.write_structured_table(table)

    notes = [_risk_summary(report)]
    notes.extend(_activity_warning_lines(report))
    if data.latest_transfer_runbook:
        notes.append(f"Latest transfer runbook: {data.latest_transfer_runbook}")
    elif data.latest_database_runbook:
        notes.append(f"Latest database runbook: {data.latest_database_runbook}")
    notes.append(
        "Next: Workstation handoff [10]/h, or restore-check [Backup→5] / System Deployment [8]."
    )
    if checklist.handoff_status == "complete":
        notes.append("Handoff complete — use [3] for the receiving-workstation guide on the target host.")
    _write_dense(notes)

    render_submenu(
        [
            ("1", "Refresh"),
            ("2", "Open workstation handoff"),
            ("3", "Receiving workstation guide"),
            ("4", "Open system deployment"),
        ],
        indent=0,
    )


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
        if choice == "2":
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu(interactive=True)
            data = _load_recovery_screen()
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            from mercury.handoff.terminal import print_receiver_handoff_guide

            print_receiver_handoff_guide(checklist=build_receiver_handoff_guide())
            show_title = pause_and_redraw()
            continue
        if choice == "4":
            from mercury.deploy.interactive_menu import run_deploy_menu

            run_deploy_menu(interactive=True)
            data = _load_recovery_screen()
            show_title = pause_and_redraw()
            continue
        menu_display.write_status("fail", menu_prompts.invalid_choice_message(choice))
