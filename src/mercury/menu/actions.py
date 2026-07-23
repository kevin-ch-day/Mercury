"""Lazy action registry for the Mercury operator console."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mercury.menu.options import (
    ACTION_BACKUP,
    ACTION_DEPLOY,
    ACTION_DOCTOR,
    ACTION_ENVIRONMENT,
    ACTION_HANDOFF,
    ACTION_HDD_STORAGE,
    ACTION_INVENTORY,
    ACTION_OFFLINE_REPOS,
    ACTION_RECOVERY,
    ACTION_REPORTS,
    ACTION_SYNC,
    main_menu_action_id_for_key,
    main_menu_action_requires_writes,
)


@dataclass(frozen=True)
class MenuAction:
    key: str
    title: str
    runner: Callable[[], None]
    action_id: str = ""


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
        run_offline_repo_menu,
        run_storage_menu,
    )

    runners_by_action: dict[str, Callable[[], None]] = {
        ACTION_HDD_STORAGE: run_storage_menu,
        ACTION_BACKUP: run_backup_batch_menu,
        ACTION_SYNC: run_sync_plan,
        ACTION_REPORTS: run_reports_and_history,
        ACTION_OFFLINE_REPOS: run_offline_repo_menu,
        ACTION_ENVIRONMENT: run_environment_check,
        ACTION_INVENTORY: run_discover_databases,
        ACTION_DOCTOR: run_doctor_menu,
        ACTION_DEPLOY: run_deploy_menu,
        ACTION_RECOVERY: run_recovery_menu,
        ACTION_HANDOFF: run_handoff_menu,
    }
    result: dict[str, MenuAction] = {}
    for item in menu_display.menu_items_by_key().values():
        action_id = main_menu_action_id_for_key(item.key)
        if action_id is None or action_id not in runners_by_action:
            continue
        result[item.key] = MenuAction(
            key=item.key,
            title=item.title.split("  ")[0],
            runner=runners_by_action[action_id],
            action_id=action_id,
        )
    return result


def resolve_menu_action(choice: str) -> MenuAction | None:
    """Return a configured action for the normalized menu choice."""
    return menu_actions().get(choice)


def menu_action_blocked_for_writes(action: MenuAction) -> bool:
    """True when the action needs HDD writes but host maintenance has them off."""
    from mercury.storage.host_maintenance import writes_allowed

    if not action.action_id:
        return False
    if not main_menu_action_requires_writes(action.action_id):
        return False
    return not writes_allowed()
