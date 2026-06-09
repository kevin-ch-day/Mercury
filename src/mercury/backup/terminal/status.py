"""Terminal output for aggregate backup source protection status."""

from __future__ import annotations

from mercury.backup.status import BackupStatusReport
from mercury.terminal import screen as display_screen


def print_backup_status_report(report: BackupStatusReport) -> None:
    display_screen.open_screen("Backup status")
    display_screen.write_fields(
        {
            "Backup root": report.backup_root,
            "Backup root state": report.backup_root_state,
            "Source databases": report.source_count,
            "Verified": report.verified_count,
            "Missing": report.missing_count,
            "Failed": report.failed_count,
        }
    )
    display_screen.write_blank()

    rows = [
        [entry.database, entry.role, entry.protection_status, entry.backup_id or "—"]
        for entry in report.entries
    ]
    if rows:
        display_screen.write_compact_table(
            ["DATABASE", "ROLE", "STATUS", "LATEST BACKUP"],
            rows,
            min_col_widths=[28, 8, 14, 20],
            max_col_widths=[36, 10, 18, 38],
        )
    else:
        display_screen.write_status("warn", "No source databases in active backup scope.")

    if report.warnings:
        display_screen.write_blank()
        for warning in report.warnings:
            display_screen.write_status("warn", warning)
