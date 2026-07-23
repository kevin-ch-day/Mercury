"""Interactive handoff checklist, wizard, and write helpers."""

from __future__ import annotations

from mercury.core.runtime import should_probe_database_status
from mercury.handoff.display import handoff_pipeline_line, handoff_wizard_plan_line
from mercury.handoff.history import build_handoff_history
from mercury.handoff.receiver import build_receiver_handoff_guide
from mercury.handoff.snapshot import build_handoff_snapshot, clear_handoff_snapshot
from mercury.handoff.terminal import (
    print_handoff_checklist,
    print_handoff_status_panel,
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
    from mercury.handoff.menu_options import handoff_menu_render_options
    from mercury.storage.host_maintenance import writes_allowed

    display_screen.write_blank()
    render_submenu(handoff_menu_render_options(writes_allowed=writes_allowed()), indent=0)


def _render_handoff_tools() -> None:
    from mercury.handoff.menu_options import handoff_tools_render_options
    from mercury.storage.host_maintenance import writes_allowed

    display_screen.open_screen("Handoff Tools")
    render_submenu(
        handoff_tools_render_options(writes_allowed=writes_allowed()),
        indent=0,
    )


def _run_handoff_tools(snapshot) -> None:
    from mercury.backup.write_preflight import assess_backup_write_preflight

    _render_handoff_tools()
    choice = read_submenu_choice()
    if choice in {None, "0"}:
        return
    write_choices = {"1", "2", "3", "4", "5", "6"}
    if choice in write_choices:
        preflight = assess_backup_write_preflight()
        if not preflight.allowed:
            display_screen.write_status(
                "fail",
                "Handoff Tools write refused: Mercury HDD detach maintenance is active.",
            )
            display_screen.write_summary(preflight.reason)
            return
    if choice == "1":
        _run_guided_wizard(start_phase="verify")
    elif choice == "2":
        _show_phase_result(run_handoff_backup_phase(live=should_probe_database_status(), execute=True))
        _after_handoff_write()
    elif choice == "3":
        _show_phase_result(run_handoff_verify_phase(execute=True))
        _after_handoff_write()
    elif choice == "4":
        _show_phase_result(run_handoff_repo_bundle_phase(execute=True))
        _after_handoff_write()
    elif choice == "5":
        _show_phase_result(run_handoff_db_bundle_phase(live=should_probe_database_status(), execute=True, confirm=_menu_confirm))
        _after_handoff_write()
    elif choice == "6":
        _show_phase_result(run_handoff_transfer_phase(live=should_probe_database_status(), execute=True, confirm=_menu_confirm))
        _after_handoff_write()
    elif choice == "7":
        print_handoff_checklist(snapshot.checklist)
    elif choice == "8":
        print_handoff_history(build_handoff_history())
    else:
        display_screen.write_summary(menu_prompts.invalid_choice_message(choice))


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
    """Destination checklist/status (package-first). Write tools live under Advanced."""
    show_title = True
    while True:
        snapshot = build_handoff_snapshot(
            live=should_probe_database_status(),
            refresh=show_title,
        )
        if interactive:
            print_handoff_status_panel(snapshot.checklist)
        else:
            print_handoff_checklist(snapshot.checklist)
        if not interactive:
            return
        display_screen.write_blank()
        render_submenu(
            [
                ("1", "Refresh destination package status"),
                ("2", "Open receiver guide (pinned package)"),
                ("3", "Advanced handoff tools"),
                ("0", "Back"),
            ],
            indent=0,
        )
        show_title = False
        choice = read_submenu_choice()
        if choice is None or choice == "0":
            return
        if choice == "1":
            clear_handoff_snapshot()
            show_title = pause_and_redraw()
            continue
        if choice == "2":
            print_receiver_handoff_guide(checklist=build_receiver_handoff_guide())
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            run_advanced_handoff_tools()
            show_title = pause_and_redraw()
            continue
        display_screen.write_summary(menu_prompts.invalid_choice_message(choice))
        show_title = False


def run_advanced_handoff_tools() -> None:
    """Historical transfer / package-build / worktree tools (expert path)."""
    from mercury.handoff.display import step_progress_summary

    show_title = True
    while True:
        snapshot = build_handoff_snapshot(
            live=should_probe_database_status(),
            refresh=show_title,
        )
        display_screen.open_screen("Advanced handoff tools")
        display_screen.write_summary(
            "Historical transfer manifests, package building, and worktree capture."
        )
        display_screen.write_fields(
            {
                "Latest transfer": snapshot.checklist.latest_transfer_age or "none",
                "Database package": snapshot.checklist.database_package,
                "Repository package": snapshot.checklist.repository_package,
                "Checklist": step_progress_summary(snapshot.checklist.steps),
            }
        )
        _render_handoff_options()
        show_title = False
        choice = read_submenu_choice()
        if choice is None or choice == "0":
            return
        if choice == "1":
            clear_handoff_snapshot()
            show_title = pause_and_redraw()
            continue
        if choice == "2":
            from mercury.backup.write_preflight import assess_backup_write_preflight

            preflight = assess_backup_write_preflight()
            if not preflight.allowed:
                display_screen.write_status(
                    "fail",
                    "Guided handoff write refused: Mercury HDD detach maintenance is active.",
                )
                display_screen.write_summary(preflight.reason)
            else:
                _run_guided_wizard()
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            from mercury.backup.write_preflight import assess_backup_write_preflight
            from mercury.migration.web_capture import capture_web_worktrees

            preflight = assess_backup_write_preflight()
            if not preflight.allowed:
                display_screen.write_status(
                    "fail",
                    "Web capture refused: Mercury HDD detach maintenance is active.",
                )
                display_screen.write_summary(preflight.reason)
                show_title = pause_and_redraw()
                continue

            results = capture_web_worktrees(execute=False)
            for result in results:
                display_screen.write_summary(f"Preview: {result.name} → {result.snapshot_dir}")
            if menu_prompts.ask_yes_no(
                "Write restricted web snapshots to active operator storage?", default=False
            ):
                results = capture_web_worktrees(execute=True)
                for result in results:
                    display_screen.write_summary(
                        f"{result.name}: {'restore checked' if result.restore_checked else result.error or 'not captured'}"
                    )
            show_title = pause_and_redraw()
            continue
        if choice == "4":
            print_receiver_handoff_guide(checklist=build_receiver_handoff_guide())
            show_title = pause_and_redraw()
            continue
        if choice == "5":
            _run_handoff_tools(snapshot)
            show_title = pause_and_redraw()
            continue
        display_screen.write_summary(menu_prompts.invalid_choice_message(choice))
        show_title = False
