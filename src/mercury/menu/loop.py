"""Interactive menu loop and choice routing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts

MenuResult = Literal["exit", "continue", "invalid", "empty"]


def _menu_actions() -> dict[str, tuple[str, Callable[[], None]]]:
    """Lazy import so action runners can live in ``menu`` without import cycles."""
    from mercury.menu.runners import (
        run_backup_batch_menu,
        run_discover_databases,
        run_environment_check,
        run_reports_and_history,
        run_restore_check_menu,
        run_schema_backup_plan,
        run_sync_plan,
        run_verify_plan,
    )

    runners: dict[str, Callable[[], None]] = {
        "1": run_environment_check,
        "2": run_discover_databases,
        "3": run_backup_batch_menu,
        "4": run_schema_backup_plan,
        "5": run_verify_plan,
        "6": run_sync_plan,
        "7": run_restore_check_menu,
        "8": run_reports_and_history,
    }
    return {
        item.key: (item.title, runners[item.key])
        for item in menu_display.menu_items_by_key().values()
        if item.key in runners
    }


def handle_menu_choice(choice: str) -> MenuResult:
    """Handle one menu selection."""
    from mercury.logging.events import log_menu_action
    from mercury.logging import get_logger, log_operation

    normalized = menu_prompts.normalize_menu_choice(choice)
    get_logger("mercury.menu").info("menu choice raw=%r normalized=%r", choice, normalized)
    if choice.strip() == "?":
        output.write(menu_display.render_menu_help())
        return "empty"
    if not normalized:
        return "empty"
    if normalized == "0":
        menu_display.write_summary("Exiting Mercury.")
        log_menu_action(choice=normalized, title="Exit", result="exit")
        return "exit"

    actions = _menu_actions()
    action = actions.get(normalized)
    if action is None:
        menu_display.write_status("fail", menu_prompts.invalid_choice_message(choice))
        log_menu_action(choice=normalized, title="Invalid", result="invalid")
        return "invalid"

    title, runner = action
    menu_display.open_screen(title)
    with log_operation(title, logger_name="mercury.menu", choice=normalized):
        runner()
    log_menu_action(choice=normalized, title=title, result="continue")
    return "continue"


def run_menu(interactive: bool = True, *, render_menu_text: Callable[[], str] | None = None) -> None:
    """Show the Mercury menu. In interactive mode, loop until exit."""
    render = render_menu_text or _default_render_menu_text
    output.write(render())

    if not interactive:
        return

    while True:
        choice = menu_prompts.read_menu_option()
        if choice is None:
            menu_display.write_summary("Exiting Mercury.")
            break

        result = handle_menu_choice(choice)
        if result == "exit":
            break
        if result in {"invalid", "empty"}:
            continue

        output.write("")
        output.write(menu_display.render_main_menu_body())


def _default_render_menu_text() -> str:
    from mercury.menu.runners import render_menu_text

    return render_menu_text()
