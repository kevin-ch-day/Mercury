"""Display restore-check plans."""

from mercury import output
from mercury.restore.check import RestoreCheckPlan


def print_restore_check_plan(plan: RestoreCheckPlan) -> None:
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
