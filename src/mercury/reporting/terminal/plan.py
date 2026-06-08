"""Plain-text display for schema, sync, and policy dry-run plans."""

from datetime import datetime, timezone
from pathlib import Path

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.backup.layout import build_backup_layout
from mercury.backup.schema_plan import SchemaBackupPlanDryRun
from mercury.core.execution_policy import load_execution_policy
from mercury.sync.sync_plan import SyncPlanDryRun


def _format_excluded_line(name: str, reason: str) -> str:
    """Short exclusion line for schema plan."""
    if "Development database" in reason:
        return f"{name} — development database; disposable, not backup source"
    if "Restore-check" in reason:
        return f"{name} — restore-check temp DB"
    if "Unknown role" in reason or "manual review" in reason.lower():
        return f"{name} — unknown role; manual review required"
    return f"{name} — {reason}"


def _display_backup_path(backup_root: Path, relative_path: str) -> str:
    relative = Path(relative_path)
    try:
        relative = relative.relative_to("backups")
    except ValueError:
        pass
    return str((backup_root / relative).resolve())


def print_schema_backup_plan(
    plan: SchemaBackupPlanDryRun,
    *,
    compact: bool = False,
    menu: bool = False,
) -> None:
    """M4 schema-only backup plan format."""
    if compact:
        display_screen.write_fields({"sources": len(plan.sources), "excluded": len(plan.excluded)})
        if plan.sources:
            display_screen.write_blank()
            display_screen.write_table(["DATABASE"], [[name] for name in plan.sources])
        return

    for line in display_format.format_report_header("SCHEMA-ONLY BACKUP PLAN"):
        output.write(line)

    policy = load_execution_policy()
    instant = datetime.now(timezone.utc)
    plan_date = instant.strftime("%Y-%m-%d")
    plan_timestamp = instant.strftime("%Y%m%d_%H%M%S") + f"_{instant.microsecond // 1000:03d}"
    output.write("Schema backup sources:")
    if not plan.sources:
        output.write("  (none)")
    for name in plan.sources:
        layout = build_backup_layout(name, date=plan_date, timestamp=plan_timestamp)
        output.write(f"- {name}")
        output.write(
            f"  future: {_display_backup_path(policy.backup_root, layout.future_schema_hint())}"
        )

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


def print_sync_plan(plan: SyncPlanDryRun, *, compact: bool = False) -> None:
    if compact:
        display_screen.write_section("Production sync pairs")
        rows = []
        for entry in plan.entries:
            pair = display_format.format_pair(entry.source, entry.target)
            status = entry.blocked_reason or "ready"
            rows.append([pair, entry.project or "", status])
        display_screen.write_table(["PAIR", "PROJECT", "STATUS"], rows)
        return

    output.heading("Production sync plan (dry-run)")
    output.field("enabled", plan.enabled)
    output.field("confirmation_phrase", plan.confirmation_phrase)

    output.heading("Planned sync pairs")
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
    from mercury.database.terminal.policy import print_policy_report as _print

    _print(report)
