"""Main menu option definitions (single source of truth for numbering + hints)."""

from __future__ import annotations

from typing import Final

# Stable action ids — completion hints and tests must use these, not raw numbers.
ACTION_HDD_STORAGE = "hdd_storage"
MAIN_STORAGE = ACTION_HDD_STORAGE  # alias preferred in lifecycle docs / routing review
ACTION_BACKUP = "backup_sources"
ACTION_SYNC = "sync_prod_dev"
ACTION_REPORTS = "reports_history"
ACTION_OFFLINE_REPOS = "offline_repos"
ACTION_ENVIRONMENT = "environment_details"
ACTION_INVENTORY = "database_inventory"
ACTION_DOCTOR = "system_doctor"
ACTION_DEPLOY = "system_deployment"
ACTION_RECOVERY = "disaster_recovery"
ACTION_HANDOFF = "workstation_handoff"

# (key, title, action_id, requires_hdd_writes)
MAIN_MENU_OPTIONS: Final[list[tuple[str, str, str, bool]]] = [
    ("1", "Mercury HDD and Storage", ACTION_HDD_STORAGE, False),
    ("2", "Backup source databases", ACTION_BACKUP, True),
    ("3", "Sync production to development", ACTION_SYNC, True),
    ("4", "Reports and backup history", ACTION_REPORTS, False),
    ("5", "Sync offline GitHub repositories", ACTION_OFFLINE_REPOS, False),
    ("6", "Environment details", ACTION_ENVIRONMENT, False),
    ("7", "Database inventory", ACTION_INVENTORY, False),
    ("8", "System doctor and repair guide", ACTION_DOCTOR, False),
    ("9", "System deployment", ACTION_DEPLOY, False),
    ("10", "Disaster recovery", ACTION_RECOVERY, False),
    ("11", "Workstation handoff", ACTION_HANDOFF, False),
]

WRITES_DISABLED_SUFFIX = "unavailable · writes disabled"
REHEARSAL_SUFFIX = "unavailable · rehearsal active"
HDD_ABSENT_SUFFIX = "unavailable"
REPORTS_LIMITED_SUFFIX = "limited · host-local only"


def main_menu_option_by_action(action_id: str) -> tuple[str, str]:
    for key, title, action, _needs_writes in MAIN_MENU_OPTIONS:
        if action == action_id:
            return key, title
    raise KeyError(f"Unknown main menu action: {action_id}")


def main_menu_hint(action_id: str) -> str:
    """Operator hint that stays synchronized with menu numbering."""
    key, title = main_menu_option_by_action(action_id)
    return f"{title} [{key}]"


def main_menu_next(action_id: str) -> str:
    return f"Next: {main_menu_hint(action_id)}"


def main_menu_items(
    *,
    writes_allowed: bool = True,
    hdd_detached: bool = False,
    destination_rehearsal: bool = False,
) -> list[tuple[str, str]]:
    """Return ``(key, title)`` pairs for rendering, with availability suffixes."""
    items: list[tuple[str, str]] = []
    for key, title, action, needs_writes in MAIN_MENU_OPTIONS:
        suffix = ""
        if needs_writes and (not writes_allowed or hdd_detached):
            if hdd_detached:
                suffix = HDD_ABSENT_SUFFIX
            elif destination_rehearsal and action == ACTION_SYNC:
                suffix = REHEARSAL_SUFFIX
            else:
                suffix = WRITES_DISABLED_SUFFIX
        elif hdd_detached and action == ACTION_REPORTS:
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


def main_menu_action_id_for_key(key: str) -> str | None:
    for option_key, _title, action, _needs in MAIN_MENU_OPTIONS:
        if option_key == key:
            return action
    return None
