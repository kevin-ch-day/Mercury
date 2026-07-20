"""Terminal output for aggregate backup source protection status."""

from __future__ import annotations

from mercury.backup.freshness import (
    OPERATOR_FRESHNESS_GUIDANCE,
    display_artifact_status_label,
    display_freshness_label,
    handoff_freshness_warning,
)
from mercury.backup.status import BackupStatusReport
from mercury.terminal import screen as display_screen


def print_backup_status_report(report: BackupStatusReport) -> None:
    display_screen.open_screen("Backup status")
    display_screen.write_fields(
        {
            "Backup root": report.backup_root,
            "Backup root state": "operator storage mounted" if report.backup_root_state in {"operator-mounted", "usb-mounted"} else report.backup_root_state,
            "Source databases": report.source_count,
            "Artifact verified": report.verified_count,
            "Missing": report.missing_count,
            "Failed": report.failed_count,
            "Freshness stale": report.stale_count,
            "Freshness unknown": report.unknown_freshness_count,
        }
    )
    display_screen.write_blank()

    rows = [
        [
            entry.database,
            entry.role,
            display_artifact_status_label(entry.protection_status),
            display_freshness_label(entry.freshness),
            entry.backup_age or "—",
            entry.backup_id or "—",
        ]
        for entry in report.entries
    ]
    if rows:
        display_screen.write_compact_table(
            ["DATABASE", "ROLE", "ARTIFACT", "FRESHNESS", "BACKUP AGE", "LATEST BACKUP"],
            rows,
            min_col_widths=[28, 8, 10, 10, 10, 20],
            max_col_widths=[36, 10, 12, 12, 12, 38],
        )
    else:
        display_screen.write_status("warn", "No source databases in active backup scope.")

    if report.warnings:
        display_screen.write_blank()
        for warning in report.warnings:
            display_screen.write_status("warn", warning)

    empty_sources = [entry.database for entry in report.entries if entry.source_is_empty]
    if empty_sources:
        display_screen.write_blank()
        display_screen.write_status(
            "info",
            "Empty live source(s): " + ", ".join(empty_sources)
            + ". A verified artifact preserves the empty schema without activity-freshness probing.",
        )

    freshness_warning = handoff_freshness_warning(
        stale_count=report.stale_count,
        unknown_count=report.unknown_freshness_count,
    )
    if freshness_warning:
        display_screen.write_blank()
        display_screen.write_status("warn", freshness_warning)

    display_screen.write_blank()
    display_screen.write_summary(OPERATOR_FRESHNESS_GUIDANCE)
