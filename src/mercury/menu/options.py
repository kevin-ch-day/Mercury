"""Main menu option definitions (single source of truth for numbering + hints)."""

from __future__ import annotations

from typing import Final

# Phase 3 task-based main console action IDs.
MAIN_BACKUP_SYNC = "main_backup_sync"
MAIN_STORAGE = "hdd_storage"
MAIN_RECOVERY = "main_recovery"
MAIN_REPORTS = "reports_history"
MAIN_MIGRATION = "main_migration"
MAIN_HEALTH = "main_health"
MAIN_ADVANCED = "main_advanced"

# Backward-compatible aliases (expert / legacy call sites).
ACTION_HDD_STORAGE = MAIN_STORAGE
ACTION_BACKUP = MAIN_BACKUP_SYNC  # Phase 3: Backup and Sync is the primary backup entry
ACTION_BACKUP_LEGACY = "backup_sources"  # expert backup submenu still exists under Advanced
ACTION_SYNC = "sync_prod_dev"
ACTION_REPORTS = MAIN_REPORTS
ACTION_OFFLINE_REPOS = "offline_repos"
ACTION_ENVIRONMENT = "environment_details"
ACTION_INVENTORY = "database_inventory"
ACTION_DOCTOR = "system_doctor"
ACTION_DEPLOY = "system_deployment"
ACTION_RECOVERY = MAIN_RECOVERY
ACTION_RECOVERY_LEGACY = "disaster_recovery"
ACTION_HANDOFF = "workstation_handoff"

# (key, title, action_id, requires_hdd_writes)
MAIN_MENU_OPTIONS: Final[list[tuple[str, str, str, bool]]] = [
    ("1", "Back up and sync this workstation", MAIN_BACKUP_SYNC, True),
    ("2", "Mercury HDD and Storage", MAIN_STORAGE, False),
    ("3", "Restore and disaster recovery", MAIN_RECOVERY, False),
    ("4", "Reports and backup history", MAIN_REPORTS, False),
    ("5", "Workstation migration", MAIN_MIGRATION, False),
    ("6", "System health and configuration", MAIN_HEALTH, False),
    ("7", "Advanced tools", MAIN_ADVANCED, False),
]

# Software-only console when the Mercury HDD is absent.
SOFTWARE_ONLY_MENU_OPTIONS: Final[list[tuple[str, str, str, bool]]] = [
    ("1", "Reconnect or configure Mercury HDD", MAIN_STORAGE, False),
    ("2", "Restore and disaster recovery planning", MAIN_RECOVERY, False),
    ("3", "Reports available on this host", MAIN_REPORTS, False),
    ("4", "System health and configuration", MAIN_HEALTH, False),
    ("5", "Advanced software-only tools", MAIN_ADVANCED, False),
]

WRITES_DISABLED_SUFFIX = "unavailable · writes disabled"
REHEARSAL_SUFFIX = "unavailable · rehearsal active"
HDD_ABSENT_SUFFIX = "unavailable"
REPORTS_LIMITED_SUFFIX = "limited · host-local only"
HDD_REQUIRED_SUFFIX = "requires Mercury HDD"


def _active_menu_options(*, software_only: bool = False) -> list[tuple[str, str, str, bool]]:
    return list(SOFTWARE_ONLY_MENU_OPTIONS if software_only else MAIN_MENU_OPTIONS)


def main_menu_option_by_action(
    action_id: str, *, software_only: bool = False
) -> tuple[str, str]:
    # Map legacy expert action ids onto the Phase 3 task that owns them.
    legacy_aliases = {
        ACTION_BACKUP_LEGACY: MAIN_BACKUP_SYNC,
        ACTION_SYNC: MAIN_ADVANCED,
        ACTION_OFFLINE_REPOS: MAIN_ADVANCED,
        ACTION_ENVIRONMENT: MAIN_HEALTH,
        ACTION_INVENTORY: MAIN_HEALTH,
        ACTION_DOCTOR: MAIN_HEALTH,
        ACTION_DEPLOY: MAIN_MIGRATION,
        ACTION_RECOVERY_LEGACY: MAIN_RECOVERY,
        ACTION_HANDOFF: MAIN_MIGRATION,
        "disaster_recovery": MAIN_RECOVERY,
        "workstation_handoff": MAIN_MIGRATION,
        "backup_sources": MAIN_BACKUP_SYNC,
        "sync_prod_dev": MAIN_ADVANCED,
        "offline_repos": MAIN_ADVANCED,
        "environment_details": MAIN_HEALTH,
        "database_inventory": MAIN_HEALTH,
        "system_doctor": MAIN_HEALTH,
        "system_deployment": MAIN_MIGRATION,
    }
    resolved = legacy_aliases.get(action_id, action_id)
    for key, title, action, _needs_writes in _active_menu_options(software_only=software_only):
        if action == resolved:
            return key, title
    for key, title, action, _needs_writes in MAIN_MENU_OPTIONS:
        if action == resolved:
            return key, title
    raise KeyError(f"Unknown main menu action: {action_id}")


def main_menu_hint(action_id: str, *, software_only: bool = False) -> str:
    """Operator hint that stays synchronized with menu numbering."""
    key, title = main_menu_option_by_action(action_id, software_only=software_only)
    return f"{title} [{key}]"


def main_menu_next(action_id: str, *, software_only: bool = False) -> str:
    return f"Next: {main_menu_hint(action_id, software_only=software_only)}"


def main_menu_items(
    *,
    writes_allowed: bool = True,
    hdd_detached: bool = False,
    destination_rehearsal: bool = False,
    software_only: bool = False,
) -> list[tuple[str, str]]:
    """Return ``(key, title)`` pairs for rendering, with availability suffixes."""
    items: list[tuple[str, str]] = []
    for key, title, action, needs_writes in _active_menu_options(software_only=software_only):
        suffix = ""
        # Backup and Sync remains selectable while writes are disabled so the
        # guided session can offer governed writer restoration.
        if (
            needs_writes
            and action != MAIN_BACKUP_SYNC
            and (not writes_allowed or hdd_detached)
        ):
            if hdd_detached:
                suffix = HDD_ABSENT_SUFFIX
            else:
                suffix = WRITES_DISABLED_SUFFIX
        elif hdd_detached and action == MAIN_REPORTS:
            suffix = REPORTS_LIMITED_SUFFIX
        if suffix:
            items.append((key, f"{title}  {suffix}"))
        else:
            items.append((key, title))
    return items


def main_menu_action_requires_writes(action_id: str) -> bool:
    for _key, _title, action, needs_writes in MAIN_MENU_OPTIONS:
        if action == action_id:
            return needs_writes
    return False


def main_menu_action_id_for_key(key: str, *, software_only: bool = False) -> str | None:
    for option_key, _title, action, _needs in _active_menu_options(software_only=software_only):
        if option_key == key:
            return action
    return None


def main_menu_max_primary_actions(*, software_only: bool = False) -> int:
    return len(_active_menu_options(software_only=software_only))
