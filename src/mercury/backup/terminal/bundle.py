"""Terminal output for database backup transfer bundle planning."""

from __future__ import annotations

from mercury.backup.bundle import DatabaseBundlePlan
from mercury.terminal import screen as display_screen


def print_database_bundle_plan(plan: DatabaseBundlePlan, *, executed: bool = False) -> None:
    title = "Database backup bundle"
    display_screen.open_screen(title)
    display_screen.write_fields(
        {
            "Backup root": str(plan.backup_root),
            "Manifest dir": str(plan.manifest_dir),
            "Runbook dir": str(plan.runbook_dir),
            "Source databases": plan.source_count,
            "Verified": plan.verified_count,
            "Missing": plan.missing_count,
            "Failed": plan.failed_count,
        }
    )
    display_screen.write_blank()
    rows = [
        [
            entry.database,
            entry.role,
            entry.protection_status,
            entry.backup_id or "—",
        ]
        for entry in plan.entries
    ]
    if rows:
        display_screen.write_compact_table(
            ["DATABASE", "ROLE", "STATUS", "LATEST BACKUP"],
            rows,
            min_col_widths=[28, 8, 14, 20],
            max_col_widths=[36, 10, 18, 38],
        )
    if plan.warnings:
        display_screen.write_blank()
        for warning in plan.warnings:
            display_screen.write_status("warn", warning)
    display_screen.write_blank()
    if executed:
        display_screen.write_summary(f"Index manifest: {plan.planned_index_manifest_path}")
        display_screen.write_summary(f"Index runbook: {plan.planned_index_runbook_path}")
    else:
        display_screen.write_summary(f"Planned index manifest: {plan.planned_index_manifest_path}")
        display_screen.write_summary(f"Planned index runbook: {plan.planned_index_runbook_path}")
