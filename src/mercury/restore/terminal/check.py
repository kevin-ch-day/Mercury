"""Display restore-check plans."""

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.restore.check_plan import RestoreCheckPlan


def _compact_restore_status(plan: RestoreCheckPlan) -> str:
    if plan.allowed:
        return "ready"
    if "Backup root is repo-local fallback; configure operator-storage backups before restore-check." in plan.blockers:
        return "Operator storage root required"
    if "No on-disk backup found for production source." in plan.blockers:
        return "missing verified backup"
    if "Latest backup is not artifact-verified." in plan.blockers:
        return "backup not verified"
    if plan.blockers:
        return plan.blockers[0]
    return "blocked"


def print_restore_check_plans(
    plans: list[RestoreCheckPlan],
    *,
    compact: bool = False,
    menu: bool = False,
) -> None:
    if compact:
        ready = sum(1 for plan in plans if plan.allowed)
        blocked = len(plans) - ready
        display_screen.write_fields(
            {
                "Ready sources": ready,
                "Blocked sources": blocked,
                "Plan mode": "dry-run",
            }
        )
        rows: list[list[str]] = []
        for plan in plans:
            status = _compact_restore_status(plan)
            rows.append([plan.source_prod, status])
        display_screen.write_blank()
        table = Table.from_headers(
            ["DATABASE", "STATUS"],
            rows,
            style=TableStyle(indent=2),
            min_col_widths=[28, 12],
            max_col_widths=[36, 24],
        )
        display_screen.write_structured_table(table)
        return

    for plan in plans:
        print_restore_check_plan(plan, compact=False)
        output.write("")


def print_restore_check_plan(plan: RestoreCheckPlan, *, compact: bool = False) -> None:
    if compact:
        print_restore_check_plans([plan], compact=True)
        return

    output.heading("RESTORE-CHECK PLAN (dry-run)")
    output.field("source_prod", plan.source_prod)
    output.field("restore_target", plan.restore_target)
    output.field("allowed", plan.allowed)
    output.field("backup_verified", plan.backup_verified)
    if plan.backup_directory:
        output.field("backup_directory", plan.backup_directory)
    if plan.backup_id:
        output.field("backup_id", plan.backup_id)
    if plan.dump_file:
        output.field("dump_file", plan.dump_file)

    if plan.target_completeness is not None:
        output.heading("Target completeness (schema/objects; not data freshness)")
        completeness = plan.target_completeness
        output.field("status", completeness.completeness_status)
        output.field("ready_for_restore_planning", completeness.ready_for_restore_planning)
        if completeness.live_object_count is not None and completeness.backup_object_count is not None:
            output.field(
                "live_vs_backup_objects",
                f"{completeness.live_object_count} live / {completeness.backup_object_count} backup baseline",
            )
        if completeness.missing_critical_tables:
            output.field("missing_critical_tables", ", ".join(completeness.missing_critical_tables))
        for blocker in completeness.blockers:
            output.bullet(blocker)
        for warning in completeness.warnings:
            output.bullet(warning)

    if plan.blockers:
        output.heading("Blockers")
        for blocker in plan.blockers:
            output.bullet(blocker)

    if plan.planned_commands:
        output.heading("Planned commands (not executed)")
        for command in plan.planned_commands:
            output.write(command)

    output.heading("Safety notes")
    for note in plan.safety_notes:
        output.bullet(note)
