"""Lazy action registry for the Mercury operator console."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mercury.menu.options import (
    MAIN_ADVANCED,
    MAIN_BACKUP_SYNC,
    MAIN_HEALTH,
    MAIN_MIGRATION,
    MAIN_RECOVERY,
    MAIN_REPORTS,
    MAIN_STORAGE,
    main_menu_action_id_for_key,
    main_menu_action_requires_writes,
)


@dataclass(frozen=True)
class MenuAction:
    key: str
    title: str
    runner: Callable[[], None]
    action_id: str = ""


def _software_only_mode() -> bool:
    from mercury.menu.recommendation import build_main_menu_recommendation

    return bool(build_main_menu_recommendation().software_only)


def menu_actions() -> dict[str, MenuAction]:
    """Return the current menu action map keyed by selection number."""
    from mercury.menu import main_display as menu_display
    from mercury.menu.runners import (
        run_advanced_hub,
        run_backup_sync_hub,
        run_health_hub,
        run_migration_hub,
        run_recovery_hub,
        run_reports_and_history,
        run_storage_menu,
    )

    software_only = _software_only_mode()
    runners_by_action: dict[str, Callable[[], None]] = {
        MAIN_BACKUP_SYNC: run_backup_sync_hub,
        MAIN_STORAGE: run_storage_menu,
        MAIN_RECOVERY: run_recovery_hub,
        MAIN_REPORTS: run_reports_and_history,
        MAIN_MIGRATION: run_migration_hub,
        MAIN_HEALTH: run_health_hub,
        MAIN_ADVANCED: run_advanced_hub,
    }
    result: dict[str, MenuAction] = {}
    for item in menu_display.menu_items_by_key().values():
        action_id = main_menu_action_id_for_key(item.key, software_only=software_only)
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
    # Guided Backup and Sync handles writer restoration itself.
    if action.action_id == MAIN_BACKUP_SYNC:
        return False
    if not main_menu_action_requires_writes(action.action_id):
        return False
    return not writes_allowed()
