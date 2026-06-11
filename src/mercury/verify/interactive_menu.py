"""Interactive backup verification menu (option 2)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.backup.terminal.verify import print_verify_menu_summary, run_verify_all_for_menu

VERIFY_SCREEN_TITLE = "Verify Backups"


def read_verify_choice() -> str | None:
    return read_submenu_choice()


def _render_verify_screen(summary, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(VERIFY_SCREEN_TITLE)
    print_verify_menu_summary(summary)
    display_screen.write_blank()
    render_submenu(
        [
            ("1", "Rescan"),
            ("2", "Verify all and update manifests"),
        ],
        indent=0,
    )


def run_verify_menu(*, interactive: bool = True) -> None:
    summary = run_verify_all_for_menu(update_manifest=False)
    show_title = True
    while True:
        _render_verify_screen(summary, show_title=show_title)
        show_title = False
        if not interactive:
            return

        choice = read_verify_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            summary = run_verify_all_for_menu(update_manifest=False)
            display_screen.write_summary(
                f"Rescanned — {summary.verified} verified, "
                f"{summary.missing} missing, {summary.failed} failed."
            )
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            summary = run_verify_all_for_menu(update_manifest=True)
            display_screen.write_summary(
                f"Verification complete — {summary.verified} verified, "
                f"{summary.missing} missing, {summary.failed} failed."
            )
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
