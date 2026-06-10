"""Display read-only restore readiness / target completeness reports."""

from __future__ import annotations

from mercury import output
from mercury.restore.readiness import (
    TARGET_COMPLETENESS_SCOPE_NOTE,
    TargetCompletenessEntry,
    TargetCompletenessReport,
)
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen


def _status_label(entry: TargetCompletenessEntry) -> str:
    if entry.completeness_status == "complete":
        return "complete"
    if entry.completeness_status == "incomplete":
        return "incomplete"
    if entry.completeness_status == "backup_unverified":
        return "backup unverified"
    if entry.completeness_status == "backup_unavailable":
        return "backup missing"
    if entry.completeness_status == "missing_target":
        return "target missing"
    if entry.completeness_status == "not_applicable":
        return "n/a"
    return "unknown"


def print_target_completeness_entry(
    entry: TargetCompletenessEntry,
    *,
    compact: bool = False,
) -> None:
    if compact:
        status = _status_label(entry)
        detail = status
        if entry.blockers:
            detail = entry.blockers[0][:48]
        display_screen.write_status(
            "ok" if entry.completeness_status == "complete" else "warn",
            f"{entry.database}: {detail}",
        )
        return

    output.heading("TARGET COMPLETENESS (read-only)")
    output.write(TARGET_COMPLETENESS_SCOPE_NOTE)
    output.write("")
    output.field("database", entry.database)
    output.field("status", entry.completeness_status)
    output.field("ready_for_restore_planning", entry.ready_for_restore_planning)
    if entry.backup_id:
        output.field("backup_id", entry.backup_id)
    if entry.backup_directory:
        output.field("backup_directory", entry.backup_directory)
    output.field("backup_verified", entry.backup_verified)

    if entry.backup_table_count is not None:
        output.field("backup_tables", entry.backup_table_count)
    if entry.backup_view_count is not None:
        output.field("backup_views", entry.backup_view_count)
    if entry.backup_object_count is not None:
        output.field("backup_objects", entry.backup_object_count)

    if entry.live_exists:
        output.field("live_tables", entry.live_table_count)
        output.field("live_views", entry.live_view_count)
        output.field("live_objects", entry.live_object_count)

    if entry.missing_critical_tables:
        output.heading("Missing critical tables")
        for name in entry.missing_critical_tables:
            output.bullet(name)

    if entry.blockers:
        output.heading("Blockers")
        for blocker in entry.blockers:
            output.bullet(blocker)

    if entry.warnings:
        output.heading("Warnings")
        for warning in entry.warnings:
            output.bullet(warning)

    if entry.notes:
        output.heading("Notes")
        for note in entry.notes:
            output.bullet(note)


def print_target_completeness_report(
    report: TargetCompletenessReport,
    *,
    compact: bool = False,
    menu: bool = False,
) -> None:
    if compact or menu:
        display_screen.write_fields(
            {
                "Backup root": report.backup_root,
                "Complete": report.complete_count,
                "Incomplete": report.incomplete_count,
                "Unknown": report.unknown_count,
            }
        )
        rows: list[list[str]] = []
        for entry in report.entries:
            rows.append(
                [
                    entry.database,
                    _status_label(entry),
                    _object_summary(entry),
                ]
            )
        display_screen.write_blank()
        display_screen.write_compact_table(
            ["DATABASE", "STATUS", "OBJECTS live/backup"],
            rows,
            min_col_widths=[28, 12, 18],
            max_col_widths=[36, 16, 24],
        )
        display_screen.write_blank()
        display_screen.write_summary(
            TARGET_COMPLETENESS_SCOPE_NOTE
            + " Does not restore or modify databases."
        )
        return

    output.heading("RESTORE READINESS / TARGET COMPLETENESS")
    output.write(TARGET_COMPLETENESS_SCOPE_NOTE)
    output.write("")
    output.field("mode", report.mode)
    output.field("backup_root", report.backup_root)
    output.field("complete", report.complete_count)
    output.field("incomplete", report.incomplete_count)
    output.field("unknown", report.unknown_count)
    output.write("")
    for entry in report.entries:
        print_target_completeness_entry(entry, compact=False)
        output.write("")


def _object_summary(entry: TargetCompletenessEntry) -> str:
    live = entry.live_object_count
    backup = entry.backup_object_count
    if live is None or backup is None:
        return "—"
    return f"{live}/{backup}"
