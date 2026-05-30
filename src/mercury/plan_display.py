"""Plain-text display for schema, sync, and policy dry-run plans."""

from mercury import output
from mercury.backup_layout import build_backup_layout
from mercury.schema_backup_plan import SchemaBackupPlanDryRun
from mercury.sync_plan import SyncPlanDryRun


def _format_excluded_line(name: str, reason: str) -> str:
    """Short exclusion line for schema plan."""
    if "Development database" in reason:
        return f"{name} — development database; disposable, not backup source"
    if "Restore-check" in reason:
        return f"{name} — restore-check temp DB"
    if "Unknown role" in reason or "manual review" in reason.lower():
        return f"{name} — unknown role; manual review required"
    return f"{name} — {reason}"


def print_schema_backup_plan(plan: SchemaBackupPlanDryRun) -> None:
    """M4 schema-only backup plan format."""
    output.write("SCHEMA-ONLY BACKUP PLAN")
    output.write("-----------------------")

    output.write("Schema backup sources:")
    if not plan.sources:
        output.write("  (none)")
    for name in plan.sources:
        layout = build_backup_layout(name)
        output.write(f"- {name}")
        output.write(f"  future: {layout.future_schema_hint()}")

    output.write("")
    output.write("Excluded:")
    if not plan.excluded:
        output.write("  (none)")
    for item in plan.excluded:
        output.write(f"- {_format_excluded_line(item.name, item.reason)}")

    output.write("")
    output.write("Safety notes:")
    for note in plan.notes:
        output.write(f"- {note}")


def print_sync_plan(plan: SyncPlanDryRun) -> None:
    output.heading("Production to development sync plan (dry-run)")
    output.field("enabled", plan.enabled)
    output.field("confirmation_phrase", plan.confirmation_phrase)

    output.heading("Planned syncs")
    if not plan.entries:
        output.item("(none)")
    for entry in plan.entries:
        status = "ready (plan only)" if entry.target_present and not entry.blocked_reason else "blocked"
        project = f" [{entry.project}]" if entry.project else ""
        output.item(f"{entry.source} -> {entry.target}{project} [{status}]")
        for prereq in entry.prerequisites:
            output.item(f"prerequisite: {prereq}", indent=4)
        if entry.blocked_reason:
            output.item(entry.blocked_reason, indent=4)

    if plan.skipped:
        output.heading("Skipped (missing dev target)")
        for name in plan.skipped:
            output.item(name)

    output.heading("Notes")
    for note in plan.notes:
        output.bullet(note)


def print_policy_report(report) -> None:
    from mercury.database.display_policy import print_policy_report as _print

    _print(report)
