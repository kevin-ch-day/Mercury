"""Compact operator menu for HDD offline repository copies."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.repo import inspect_repositories, load_repo_definitions
from mercury.repo.offline_clone import build_offline_clone_plan, execute_offline_clone_plan
from mercury.repo.offline_terminal import print_offline_clone_plan, print_offline_sync_receipt
from mercury.terminal import screen as display_screen
from mercury.terminal.theme import hint_text


def offline_clone_plan():
    return build_offline_clone_plan(inspect_repositories(load_repo_definitions()))


# Compatibility alias used by older tests/call sites.
_plan = offline_clone_plan


def run_offline_sync_now() -> None:
    """Confirm and sync offline HDD repository copies."""
    plan = offline_clone_plan()
    output.write(
        hint_text(
            f"Sync offline HDD worktrees → {plan.root} "
            "(committed history only; source repos untouched; dirty offline copies blocked)"
        )
    )
    if menu_prompts.ask_yes_no("Sync offline HDD repository copies now?", default=False) is not True:
        display_screen.write_status("warn", "Offline repository sync cancelled.")
        return
    print_offline_clone_plan(execute_offline_clone_plan(plan), executed=True)


def show_offline_sync_receipt() -> None:
    """Show the latest offline-sync receipt (opens its own header)."""
    print_offline_sync_receipt(offline_clone_plan())


def run_offline_repo_menu(*, interactive: bool = True) -> None:
    """Unified Git recovery home (offline status + actions on one screen)."""
    from mercury.menu.task_menus import run_repo_hub

    run_repo_hub(interactive=interactive)
