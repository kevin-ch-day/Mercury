"""Interactive handoff checklist, wizard, and write helpers."""

from __future__ import annotations

from mercury.core.runtime import should_probe_database_status
from mercury.handoff.display import handoff_pipeline_line, handoff_wizard_plan_line, suggested_menu_choice
from mercury.handoff.history import build_handoff_history
from mercury.handoff.receiver import build_receiver_handoff_guide
from mercury.handoff.snapshot import build_handoff_snapshot, clear_handoff_snapshot
from mercury.handoff.terminal import (
    print_handoff_checklist,
    print_handoff_history,
    print_handoff_wizard_result,
    print_receiver_handoff_guide,
)
from mercury.handoff.wizard import (
    HandoffWizardResult,
    run_guided_handoff_wizard,
    run_handoff_backup_phase,
    run_handoff_db_bundle_phase,
    run_handoff_repo_bundle_phase,
    run_handoff_transfer_phase,
    run_handoff_verify_phase,
)
from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.terminal import screen as display_screen


def _menu_confirm(prompt: str, *, default: bool) -> bool | None:
    return menu_prompts.ask_yes_no(prompt, default=default)


def _show_phase_result(phase) -> None:
    print_handoff_wizard_result(HandoffWizardResult(phases=[phase]))


def _after_handoff_write() -> None:
    clear_handoff_snapshot()


def _render_handoff_options() -> None:
    display_screen.write_blank()
    display_screen.write_section("Guided flow")
    render_submenu(
        [
            ("2", "Run guided handoff wizard"),
            ("3", "Resume guided wizard from verification"),
        ],
        indent=0,
    )
    display_screen.write_blank()
    display_screen.write_section("Individual phases")
    render_submenu(
        [
            ("4", "Run full backup (stale or missing sources only)"),
            ("5", "Verify all source backups"),
            ("6", "Write repository bundles to operator storage"),
            ("7", "Write DB bundle index and runbooks"),
            ("8", "Write combined transfer package to operator storage"),
        ],
        indent=0,
    )
    display_screen.write_blank()
    display_screen.write_section("Tools")
    render_submenu(
        [
            ("1", "Refresh checklist"),
            ("9", "View handoff history on operator storage"),
            ("10", "Open backup menu"),
            ("11", "Receiving workstation guide"),
        ],
        indent=0,
    )


def _run_guided_wizard(*, start_phase: str | None = None) -> None:
    display_screen.write_section("Guided handoff wizard")
    snapshot = build_handoff_snapshot(live=should_probe_database_status(), refresh=True)
    display_screen.write_fields(
        {
            "Current pipeline": handoff_pipeline_line(snapshot.checklist),
            "Wizard plan": handoff_wizard_plan_line(
                snapshot.checklist,
                start_phase=start_phase,
            ),
        }
    )
    display_screen.write_blank()
    if start_phase:
        display_screen.write_summary(
            "Resumes from verification through repository bundle, database bundle, and transfer write."
        )
    else:
        display_screen.write_summary(
            "Runs backup, verify, repository bundle, database bundle, and transfer write in order. "
            "Skips backup when all sources are already fresh."
        )
    plan = run_guided_handoff_wizard(
        live=should_probe_database_status(),
        execute=False,
        start_phase=start_phase,
        stop_on_failure=False,
    )
    print_handoff_wizard_result(plan)
    if menu_prompts.ask_yes_no("Execute guided handoff wizard now?", default=False) is not True:
        display_screen.write_summary("Guided wizard cancelled.")
        return
    result = run_guided_handoff_wizard(
        live=should_probe_database_status(),
        execute=True,
        confirm=_menu_confirm,
        start_phase=start_phase,
    )
    print_handoff_wizard_result(result)
    snapshot = build_handoff_snapshot(live=should_probe_database_status(), refresh=True)
    if snapshot.checklist.recommended_actions():
        display_screen.write_blank()
        display_screen.write_list("Remaining actions", snapshot.checklist.recommended_actions())


def run_handoff_menu(*, interactive: bool = True) -> None:
    show_title = True
    while True:
        snapshot = build_handoff_snapshot(
            live=should_probe_database_status(),
            refresh=show_title,
        )
        print_handoff_checklist(snapshot.checklist)
        if snapshot.checklist.recommended_actions() and interactive:
            display_screen.write_blank()
            display_screen.write_list(
                "Suggested next steps",
                snapshot.checklist.recommended_actions()[:3],
            )
        suggested = suggested_menu_choice(snapshot.checklist)
        if suggested and interactive:
            display_screen.write_hint(f"Suggested action: press [{suggested}]")
        if not interactive:
            return
        _render_handoff_options()
        show_title = False
        choice = read_submenu_choice()
        if choice is None:
            return
        if choice == "0":
            return
        if choice == "1":
            clear_handoff_snapshot()
            show_title = pause_and_redraw()
            continue
        if choice == "2":
            _run_guided_wizard()
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            _run_guided_wizard(start_phase="verify")
            show_title = pause_and_redraw()
            continue
        if choice == "4":
            _show_phase_result(run_handoff_backup_phase(live=should_probe_database_status(), execute=True))
            _after_handoff_write()
            show_title = pause_and_redraw()
            continue
        if choice == "5":
            _show_phase_result(run_handoff_verify_phase(execute=True))
            _after_handoff_write()
            show_title = pause_and_redraw()
            continue
        if choice == "6":
            _show_phase_result(run_handoff_repo_bundle_phase(execute=True))
            _after_handoff_write()
            show_title = pause_and_redraw()
            continue
        if choice == "7":
            _show_phase_result(
                run_handoff_db_bundle_phase(
                    live=should_probe_database_status(),
                    execute=True,
                    confirm=_menu_confirm,
                )
            )
            _after_handoff_write()
            show_title = pause_and_redraw()
            continue
        if choice == "8":
            _show_phase_result(
                run_handoff_transfer_phase(
                    live=should_probe_database_status(),
                    execute=True,
                    confirm=_menu_confirm,
                )
            )
            _after_handoff_write()
            show_title = pause_and_redraw()
            continue
        if choice == "9":
            print_handoff_history(build_handoff_history())
            show_title = pause_and_redraw()
            continue
        if choice == "10":
            from mercury.backup.interactive_menu import run_backup_menu

            run_backup_menu(interactive=True)
            clear_handoff_snapshot()
            show_title = pause_and_redraw()
            continue
        if choice == "11":
            print_receiver_handoff_guide(checklist=build_receiver_handoff_guide())
            show_title = pause_and_redraw()
            continue
        display_screen.write_summary(menu_prompts.invalid_choice_message(choice))
        show_title = False
