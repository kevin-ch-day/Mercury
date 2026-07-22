"""Compact operator menu for HDD offline repository copies."""

from __future__ import annotations

from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import read_submenu_choice, render_submenu
from mercury.repo import inspect_repositories, load_repo_definitions
from mercury.repo.offline_clone import build_offline_clone_plan, execute_offline_clone_plan
from mercury.repo.offline_terminal import print_offline_clone_plan, print_offline_sync_receipt
from mercury.terminal import screen as display_screen


def _plan():
    return build_offline_clone_plan(inspect_repositories(load_repo_definitions()))


def run_offline_repo_menu(*, interactive: bool = True) -> None:
    print_offline_clone_plan(_plan())
    while True:
        render_submenu(
            [
                ("1", "Sync Offline GitHub Repositories"),
                ("2", "View Last Sync Receipt"),
            ]
        )
        if not interactive:
            return
        choice = read_submenu_choice()
        if choice in {None, "0"}:
            return
        if choice == "1":
            plan = _plan()
            from mercury.terminal.theme import hint_text
            from mercury import output

            output.write(
                hint_text(
                    f"Sync offline HDD worktrees → {plan.root} "
                    "(committed history only; source repos untouched; dirty offline copies blocked)"
                )
            )
            if menu_prompts.ask_yes_no("Sync offline HDD repository copies now?", default=False) is not True:
                display_screen.write_status("warn", "Offline repository sync cancelled.")
            else:
                print_offline_clone_plan(execute_offline_clone_plan(plan), executed=True)
        elif choice == "2":
            print_offline_sync_receipt(_plan())
        else:
            display_screen.write_status("fail", "Choose a listed option.")
