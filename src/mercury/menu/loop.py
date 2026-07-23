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
    from mercury.menu.actions import menu_action_blocked_for_writes
    from mercury.menu.options import ACTION_HANDOFF, MAIN_MIGRATION, main_menu_hint
    from mercury.storage.lifecycle import writes_disabled_redirect_message

    normalized = menu_prompts.normalize_menu_choice(choice)
    get_logger("mercury.menu").info("menu choice raw=%r normalized=%r", choice, normalized)
    if choice.strip() == "?":
        output.write(menu_display.render_menu_help())
        return "empty"
    if not normalized:
        return "empty"
    if normalized in {"r", "repair"}:
        from mercury.repair.startup import (
            _hdd_writer_active,
            primary_mount_hint,
            run_usb_repair_flow,
        )

        if _hdd_writer_active():
            hint = primary_mount_hint()
            if hint:
                menu_display.write_status("warn", hint)
            else:
                menu_display.write_summary(
                    "HDD writer is ready. USB repair is optional archive maintenance "
                    "(./run.sh repair-usb)."
                )
            log_menu_action(choice=normalized, title="Primary mount hint", result="continue")
            return "continue"
        run_usb_repair_flow(interactive=True, default_yes=True)
        log_menu_action(choice=normalized, title="Repair USB", result="continue")
        return "continue"
    if normalized in {"h", "handoff"}:
        from mercury.menu.task_menus import run_migration_hub

        run_migration_hub()
        log_menu_action(
            choice=normalized,
            title=main_menu_hint(MAIN_MIGRATION),
            result="continue",
        )
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

    if menu_action_blocked_for_writes(action):
        output.write(writes_disabled_redirect_message())
        log_menu_action(choice=normalized, title=action.title, result="refused")
        return "continue"

    with log_operation(action.title, logger_name="mercury.menu", choice=normalized):
        action.runner()
    log_menu_action(choice=normalized, title=action.title, result="continue")
    return "continue"


def run_menu(interactive: bool = True, *, render_menu_text: Callable[[], str] | None = None) -> None:
    """Show the Mercury menu. In interactive mode, loop until exit."""
    render = render_menu_text or _default_render_menu_text
    if interactive:
        from mercury.repair.startup import (
            maybe_prompt_usb_repair_at_startup,
            primary_mount_hint,
        )
        from mercury.storage.lifecycle import maybe_prompt_storage_first_run

        maybe_prompt_storage_first_run(interactive=True)
        maybe_prompt_usb_repair_at_startup()
        hint = primary_mount_hint()
        if hint:
            from mercury.terminal import screen as display_screen

            display_screen.write_status("warn", hint)
            output.write("")

        from mercury.menu.intent import (
            OUTCOME_CANCELLED,
            OUTCOME_EXIT,
            dispatch_startup_intent,
            run_startup_intent_chooser,
            should_offer_startup_intent,
        )

        while should_offer_startup_intent():
            intent = run_startup_intent_chooser()
            outcome = dispatch_startup_intent(intent)
            if outcome == OUTCOME_EXIT:
                menu_display.write_summary("Exiting Mercury.")
                return
            if outcome == OUTCOME_CANCELLED:
                continue
            break
    else:
        from mercury.storage.lifecycle import maybe_prompt_storage_first_run

        maybe_prompt_storage_first_run(interactive=False)

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
