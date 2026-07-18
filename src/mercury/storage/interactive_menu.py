"""Interactive storage status and migration helpers (observe / gated copy)."""

from __future__ import annotations

from mercury import output
from mercury.core.storage_roles import MigrationState
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.storage.cutover_readiness import build_cutover_readiness
from mercury.storage.migrate_plan import build_migration_plan
from mercury.storage.migrate_run import patch_migration_state, run_migration
from mercury.storage.migrate_verify import verify_migration
from mercury.storage.report import build_storage_status_report
from mercury.storage.terminal import (
    print_cutover_readiness,
    print_migration_plan,
    print_migration_run,
    print_migration_verify,
    print_storage_status,
)
from mercury.terminal import screen as display_screen

STORAGE_SCREEN_TITLE = "Storage Migration"


def _render_storage_screen(*, show_title: bool) -> None:
    if show_title:
        display_screen.write_report_header(STORAGE_SCREEN_TITLE)
    report = build_storage_status_report()
    print_storage_status(report)
    display_screen.write_blank()
    display_screen.write_section("Actions")
    render_submenu(
        [
            ("1", "Refresh status"),
            ("2", "Migration plan (dry-run inventory)"),
            ("3", "Preview migrate-run (dry-run)"),
            ("4", "Verify migration"),
            ("5", "Cutover readiness checklist"),
            ("6", "Mark plan state (migration_state=planned)"),
        ],
        indent=0,
    )
    display_screen.write_blank()
    output.write(
        menu_display.format_menu_bottom_option("Back")
    )


def run_storage_menu(*, interactive: bool = True) -> None:
    """Operator console for storage roots and migration (no cutover switch)."""
    show_title = True
    while True:
        _render_storage_screen(show_title=show_title)
        show_title = False
        if not interactive:
            return
        choice = read_submenu_choice()
        if choice is None or choice == "0":
            return
        if choice == "1":
            display_screen.write_summary("Refreshed storage status.")
            show_title = pause_and_redraw()
            continue
        if choice == "2":
            plan = build_migration_plan()
            print_migration_plan(plan)
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            result = run_migration(execute=False, update_state=False)
            print_migration_run(result)
            show_title = pause_and_redraw()
            continue
        if choice == "4":
            report = verify_migration(update_state=False)
            print_migration_verify(report)
            show_title = pause_and_redraw()
            continue
        if choice == "5":
            print_cutover_readiness(build_cutover_readiness())
            show_title = pause_and_redraw()
            continue
        if choice == "6":
            plan = build_migration_plan()
            if not plan.ready_for_migrate_execute:
                display_screen.write_status(
                    "fail",
                    "Plan not ready — resolve blockers before marking planned.",
                )
            else:
                notes = patch_migration_state(MigrationState.PLANNED)
                for note in notes:
                    display_screen.write_status("warn", note)
                display_screen.write_summary(
                    "migration_state=planned (writers still on legacy)."
                )
            show_title = pause_and_redraw()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))
