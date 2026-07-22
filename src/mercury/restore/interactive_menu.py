"""Interactive restore-check menu inside the backup operations lane."""

from __future__ import annotations

from pathlib import Path

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import resolve_batch_sources
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.restore.check_plan import RestoreCheckPlan, build_restore_check_plan
from mercury.restore.check_cleanup import cleanup_restorecheck_databases, discover_restorecheck_names
from mercury.restore.terminal.check_cleanup import print_restorecheck_cleanup_batch
from mercury.restore.terminal.check import print_restore_check_plans
from mercury.restore.restore_runner import execute_restore_into_database
from mercury.restore.terminal.runner import print_restore_execution_result

RESTORE_SCREEN_TITLE = "Restore-check Operations"


def read_restore_choice() -> str | None:
    return read_submenu_choice()


def _load_plans() -> list[RestoreCheckPlan]:
    sources = resolve_batch_sources(live=should_probe_database_status())
    return [build_restore_check_plan(prod) for prod in sources]


def _allowed_plans(plans: list[RestoreCheckPlan]) -> list[RestoreCheckPlan]:
    return [plan for plan in plans if plan.allowed]


def _restorecheck_names_on_server() -> list[str]:
    return discover_restorecheck_names()


def _render_restore_screen(plans, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(RESTORE_SCREEN_TITLE)
    if not plans:
        menu_display.write_status("warn", "No backup sources found.")
    else:
        print_restore_check_plans(plans, compact=True, menu=True)
    display_screen.write_blank()
    options: list[tuple[str, str]] = [("1", "Refresh")]
    if _allowed_plans(plans):
        policy = load_execution_policy()
        label = "Run restore-checks"
        if not policy.live_execution_allowed():
            label = f"{label} (live mode required)"
        options.append(("2", label))
    else:
        options.append(("2", "Run restore-checks (none ready)"))
    restorecheck_count = len(_restorecheck_names_on_server())
    if restorecheck_count:
        options.append(("3", f"Clean up temp restore-check databases ({restorecheck_count})"))
    render_submenu(options)


def _run_allowed_restore_checks(plans: list[RestoreCheckPlan]) -> None:
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    allowed = _allowed_plans(plans)
    if not allowed:
        menu_display.write_status("warn", "No allowed restore-check plans.")
        return

    for plan in allowed:
        if not plan.dump_file or not plan.backup_directory:
            continue
        dump_path = Path(plan.backup_directory) / plan.dump_file
        result = execute_restore_into_database(
            target_database=plan.restore_target,
            dump_path=dump_path,
            source_database=plan.source_prod,
            execute=execute,
            policy=policy,
            recreate_target=True,
            cleanup_after_success=True,
        )
        print_restore_execution_result(result, compact=True)


def _cleanup_restorecheck_databases() -> None:
    from mercury.core.execution_policy import load_execution_policy

    names = _restorecheck_names_on_server()
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    batch = cleanup_restorecheck_databases(names, execute=execute)
    print_restorecheck_cleanup_batch(batch, compact=True)


def run_restore_menu(*, interactive: bool = True) -> None:
    plans = _load_plans()
    show_title = True
    while True:
        _render_restore_screen(plans, show_title=show_title)
        show_title = False
        if not interactive:
            return

        choice = read_restore_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            plans = _load_plans()
            ready = sum(1 for plan in plans if plan.allowed)
            display_screen.write_summary(f"Rescanned — {ready} ready, {len(plans) - ready} blocked.")
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            _run_allowed_restore_checks(plans)
            show_title = pause_and_redraw()
            continue

        if choice == "3":
            _cleanup_restorecheck_databases()
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
