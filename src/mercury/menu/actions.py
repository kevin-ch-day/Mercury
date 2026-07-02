"""Lazy action registry for the Mercury operator console."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MenuAction:
    key: str
    title: str
    runner: Callable[[], None]


def menu_actions() -> dict[str, MenuAction]:
    """Return the current menu action map keyed by selection number."""
    from mercury.menu import main_display as menu_display
    from mercury.menu.runners import (
        run_backup_batch_menu,
        run_reports_and_history,
        run_sync_plan,
        run_discover_databases,
        run_environment_check,
        run_doctor_menu,
        run_deploy_menu,
        run_recovery_menu,
        run_handoff_menu,
    )

    runners: dict[str, Callable[[], None]] = {
        "1": run_backup_batch_menu,
        "2": run_sync_plan,
        "3": run_reports_and_history,
        "4": run_environment_check,
        "5": run_discover_databases,
        "6": run_doctor_menu,
        "7": run_deploy_menu,
        "8": run_recovery_menu,
        "9": run_handoff_menu,
    }
    return {
        item.key: MenuAction(key=item.key, title=item.title, runner=runners[item.key])
        for item in menu_display.menu_items_by_key().values()
        if item.key in runners
    }


def resolve_menu_action(choice: str) -> MenuAction | None:
    """Return a configured action for the normalized menu choice."""
    return menu_actions().get(choice)
