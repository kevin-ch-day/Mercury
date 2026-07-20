"""Compact operator menu for HDD offline repository copies."""

from __future__ import annotations

from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.repo import inspect_repositories, load_repo_definitions
from mercury.repo.offline_clone import build_offline_clone_plan, execute_offline_clone_plan
from mercury.repo.offline_terminal import print_offline_clone_plan
from mercury.terminal import screen as display_screen


def _plan():
    return build_offline_clone_plan(inspect_repositories(load_repo_definitions()))


def run_offline_repo_menu(*, interactive: bool = True) -> None:
    show_title = True
    while True:
        if show_title:
            print_offline_clone_plan(_plan())
        render_submenu([("1", "Preview HDD repository sync"), ("2", "Sync HDD repository copies")])
        if not interactive:
            return
        choice = read_submenu_choice()
        if choice in {None, "0"}:
            return
        if choice == "1":
            print_offline_clone_plan(_plan())
        elif choice == "2":
            plan = _plan()
            display_screen.write_blank()
            display_screen.write_fields(
                {
                    "Operation": "Create or update offline HDD Git worktrees",
                    "Target": plan.root,
                    "Source": "Latest committed repository history",
                    "Safety": "Source repos untouched; dirty offline copies are blocked",
                }
            )
            if menu_prompts.ask_yes_no("Sync offline HDD repository copies now?", default=False) is not True:
                display_screen.write_status("warn", "Offline repository sync cancelled.")
            else:
                print_offline_clone_plan(execute_offline_clone_plan(plan), executed=True)
        else:
            display_screen.write_status("fail", "Choose a listed option.")
        show_title = pause_and_redraw(show_title=False)
