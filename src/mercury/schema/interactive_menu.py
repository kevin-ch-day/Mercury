"""Interactive schema export menu (option 4)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_SCHEMA_ONLY
from mercury.database import MariaDbConfigError, MariaDbLiveError, try_load_mariadb_config
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.reporting.terminal.plan import print_schema_backup_plan
from mercury.backup.schema_plan import build_schema_backup_plan_demo, build_schema_backup_plan_live
from mercury.backup.batch_runner import run_backup_batch
from mercury.backup.terminal.batch import print_backup_batch_result

SCHEMA_SCREEN_TITLE = "Export Schema-Only Copies"


def read_schema_choice() -> str | None:
    return read_submenu_choice()


def _load_plan():
    if try_load_mariadb_config() is not None:
        try:
            return build_schema_backup_plan_live()
        except (MariaDbConfigError, MariaDbLiveError):
            pass
    return build_schema_backup_plan_demo()


def _render_schema_screen(plan, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(SCHEMA_SCREEN_TITLE)
    policy = load_execution_policy()
    print_schema_backup_plan(plan, compact=True, menu=True)
    display_screen.write_blank()
    live_allowed = policy.live_execution_allowed()
    run_label = "Run schema export" if live_allowed else "Run schema export (live mode required)"
    render_submenu([("1", "Rescan plan"), ("2", run_label)])


def _run_schema_export(plan) -> None:
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    batch = run_backup_batch(
        BACKUP_KIND_SCHEMA_ONLY,
        execute=execute,
        live=should_probe_database_status(),
        sources=list(plan.sources),
    )
    print_backup_batch_result(batch, compact=True, menu=True)


def run_schema_menu(*, interactive: bool = True) -> None:
    plan = _load_plan()
    show_title = False
    while True:
        _render_schema_screen(plan, show_title=show_title)
        show_title = False
        if not interactive:
            return

        choice = read_schema_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            plan = _load_plan()
            display_screen.write_summary(f"Plan refreshed — {len(plan.sources)} source(s).")
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            _run_schema_export(plan)
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
