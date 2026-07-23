"""Compact terminal views for migration blockers and next action."""

from __future__ import annotations

from mercury import output
from mercury.migration.models import MigrationCheck, MigrationCheckState, MigrationReadinessReport
from mercury.terminal import screen as display_screen

_PRIORITY = (
    "usb_archive_receipt", "repository_bundles", "erebus_web_worktree", "scytaledroid_web_worktree", "database_backups", "storage_mirror",
    "web_runtime_configuration", "obsidiandroid_core_prod", "hdd_smart_health",
    "destination_validation", "writer_cutover_implementation",
)


def next_check(report: MigrationReadinessReport) -> MigrationCheck | None:
    by_id = {check.id: check for check in report.checks}
    for check_id in _PRIORITY:
        check = by_id.get(check_id)
        if check is not None and check.unresolved:
            return check
    return next(iter(report.unresolved_checks), None)


def _tag(check: MigrationCheck) -> str:
    if check.severity == "CHECK":
        return "CHECK"
    if check.state == MigrationCheckState.BLOCKED or check.blocking:
        return "BLOCKED"
    if check.state == MigrationCheckState.WARNING:
        return "CHECK"
    if check.state == MigrationCheckState.DECISION_NEEDED:
        return "OPEN"
    return "ACTION"


def print_migration_blockers(report: MigrationReadinessReport) -> int:
    from mercury.terminal.theme import colors_enabled, markup, styled_bracket_label
    from mercury.terminal.design_system import active_styles

    display_screen.open_screen("Migration Blockers")
    for check in report.unresolved_checks:
        tag = _tag(check)
        detail = f"{check.label}: {check.summary}"
        if colors_enabled():
            s = active_styles()
            style = s.fail if tag == "BLOCKED" else (s.warn if tag in {"CHECK", "OPEN"} else s.info)
            output.write(f"  {styled_bracket_label(tag, style)} {markup(detail, s.value)}")
        else:
            output.write(f"  [{tag}] {detail}")
    output.rule()
    cutover = report.check("writer_cutover_implementation")
    if cutover.state == MigrationCheckState.PASS:
        display_screen.write_summary("HDD writer cutover is complete; destination validation remains blocked.")
    else:
        display_screen.write_summary("Writer cutover remains blocked.")
    return 2 if report.overall_status.value == "BLOCKED" else 1


def print_migration_next(report: MigrationReadinessReport) -> int:
    display_screen.open_screen("Next Migration Action")
    check = next_check(report)
    if check is None:
        display_screen.write_summary("No open migration action.")
        return 0
    display_screen.write_fields({
        "Action": check.recommended_action or check.label,
        "Reason": check.summary,
        "Command": check.recommended_command or "See migration blockers",
    })
    return 2 if check.blocking else 1
