"""Display restore-check plans."""

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.restore.check_plan import RestoreCheckPlan


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
                "ready": ready,
                "blocked": blocked,
                "mode": "dry-run",
            }
        )
        rows: list[list[str]] = []
        for plan in plans:
            status = display_format.format_plan_status(ready=plan.allowed, blockers=plan.blockers)
            rows.append([plan.source_prod, status])
        display_screen.write_blank()
        display_screen.write_table(["DATABASE", "STATUS"], rows, max_col_widths=[36, 28])
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
