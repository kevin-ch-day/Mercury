"""Plain-text backup plan output including future layout hints."""

from mercury import output
from mercury.backup_layout import build_backup_layout, list_standard_filenames
from mercury.backup_manifest import BACKUP_KIND_LABELS
from mercury.database.planning import BackupPlanDryRun


def print_backup_plan(plan: BackupPlanDryRun, *, show_layout_hint: bool = True) -> None:
    output.heading("Backup plan (dry-run)")

    output.heading("Backup sources (full logical backups)")
    if plan.backup_sources:
        for name in plan.backup_sources:
            layout = build_backup_layout(name)
            output.item(name)
            if show_layout_hint:
                output.item(f"future: {layout.future_full_hint()}", indent=2)
    else:
        output.item("(none)")

    output.heading("Excluded databases")
    if plan.excluded:
        for item in plan.excluded:
            output.item(f"{item.name} [{item.role}]")
            output.item(item.reason, indent=4)
    else:
        output.item("(none)")

    if show_layout_hint and plan.backup_sources:
        example = plan.backup_sources[0]
        layout = build_backup_layout(example)
        output.heading("Future backup layout (not written in seed)")
        output.item(layout.directory)
        for fname in list_standard_filenames(example, layout.timestamp):
            output.item(fname, indent=2)
        output.heading("Backup kinds")
        for kind, label in BACKUP_KIND_LABELS.items():
            output.item(f"{kind}: {label}")

    output.heading("Safety notes")
    for note in plan.safety_notes:
        output.bullet(note)
