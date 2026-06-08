"""Shared helpers for interactive menu sub-screens (backup, sync, verify, …)."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.terminal.theme import submenu_block, submenu_empty_hint


def read_submenu_choice() -> str | None:
    """Read a submenu selection; empty input re-prompts, only ``0`` returns to main menu."""
    prompt = menu_prompts.submenu_option_prompt()
    while True:
        choice = menu_prompts.ask_stripped(prompt)
        if choice is None:
            return None
        if choice == "0":
            return "0"
        if choice == "":
            output.write(submenu_empty_hint())
            continue
        return choice


def pause_and_redraw(*, show_title: bool = True) -> bool:
    """Wait for continue; return ``show_title`` for the next redraw."""
    menu_prompts.wait_for_continue()
    return show_title


def render_submenu(options: list[tuple[str, str]], *, title: str | None = None) -> None:
    for line in submenu_block(
        options,
        title=title,
        bottom_label="Back",
    ):
        output.write(line)
