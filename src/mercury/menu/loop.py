"""Interactive menu loop and choice routing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.menu.actions import resolve_menu_action

MenuResult = Literal["exit", "continue", "invalid", "empty"]


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
    if normalized in {"r", "repair"}:
        from mercury.repair.startup import run_usb_repair_flow

        run_usb_repair_flow(interactive=True, default_yes=True)
        log_menu_action(choice=normalized, title="Repair USB", result="continue")
        return "continue"
    if normalized in {"h", "handoff"}:
        from mercury.handoff.interactive_menu import run_handoff_menu

        run_handoff_menu(interactive=True)
        log_menu_action(choice=normalized, title="Workstation handoff", result="continue")
        return "continue"
    if normalized == "0":
        menu_display.write_summary("Exiting Mercury.")
        log_menu_action(choice=normalized, title="Exit", result="exit")
        return "exit"

    action = resolve_menu_action(normalized)
    if action is None:
        menu_display.write_status("fail", menu_prompts.invalid_choice_message(choice))
        log_menu_action(choice=normalized, title="Invalid", result="invalid")
        return "invalid"

    with log_operation(action.title, logger_name="mercury.menu", choice=normalized):
        action.runner()
    log_menu_action(choice=normalized, title=action.title, result="continue")
    return "continue"


def run_menu(interactive: bool = True, *, render_menu_text: Callable[[], str] | None = None) -> None:
    """Show the Mercury menu. In interactive mode, loop until exit."""
    render = render_menu_text or _default_render_menu_text
    if interactive:
        from mercury.repair.startup import maybe_prompt_usb_repair_at_startup

        maybe_prompt_usb_repair_at_startup()

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
