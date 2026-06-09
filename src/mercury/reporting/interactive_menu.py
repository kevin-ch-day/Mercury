"""Interactive reports menu for backup history and protection status."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.on_disk_index import OnDiskBackupList, build_on_disk_backup_list
from mercury.backup.on_disk_index import latest_records_by_database
from mercury.backup.terminal.verify import print_on_disk_backup_list
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.database import MariaDbConfigError, MariaDbLiveError
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.reporting.protection import ProtectionReport, build_protection_report, print_protection_report

REPORTS_SCREEN_TITLE = "REPORTS AND BACKUP HISTORY"


def read_reports_choice() -> str | None:
    return read_submenu_choice()


def _load_backup_history() -> OnDiskBackupList:
    policy = load_execution_policy()
    return build_on_disk_backup_list(policy.backup_root)


def _load_protection_report() -> tuple[ProtectionReport, str | None]:
    live = should_probe_database_status()
    try:
        return build_protection_report(live=live), None
    except (MariaDbConfigError, MariaDbLiveError) as exc:
        return build_protection_report(), f"Live report unavailable: {exc}"
    except Exception as exc:  # pragma: no cover - defensive menu fallback
        return build_protection_report(), f"Report failed: {exc}"


def _render_reports_screen(
    backup_list: OnDiskBackupList,
    report: ProtectionReport,
    *,
    note: str | None,
    show_title: bool,
) -> None:
    if show_title:
        menu_display.open_screen(REPORTS_SCREEN_TITLE)
    if note:
        menu_display.write_status("warn", note)
        display_screen.write_blank()
    display_screen.write_fields(
        {
            "Backup root": str(backup_list.backup_root),
            "Latest tracked": len(latest_records_by_database(backup_list)),
            "Verified sources": report.verified_source_count,
            "Missing sources": report.missing_source_count,
            "Failed sources": report.failed_source_count,
        }
    )
    display_screen.write_blank()
    render_submenu(
        [
            ("1", "Refresh"),
            ("2", "Show backup history"),
            ("3", "Show protection status"),
        ],
        indent=0,
    )


def run_reports_menu(*, interactive: bool = True) -> None:
    backup_list = _load_backup_history()
    report, note = _load_protection_report()
    show_title = True
    while True:
        _render_reports_screen(
            backup_list,
            report,
            note=note,
            show_title=show_title,
        )
        note = None
        show_title = False
        if not interactive:
            return

        choice = read_reports_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            backup_list = _load_backup_history()
            report, note = _load_protection_report()
            display_screen.write_summary(
                f"Refreshed — {len(latest_records_by_database(backup_list))} tracked backup(s), "
                f"{report.verified_source_count} verified source(s)."
            )
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            print_on_disk_backup_list(backup_list, compact=True, menu=True)
            show_title = pause_and_redraw()
            continue

        if choice == "3":
            print_protection_report(report, compact=True)
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
