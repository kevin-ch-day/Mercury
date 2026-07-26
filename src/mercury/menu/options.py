"""Main menu option definitions (single source of truth for numbering + hints)."""

from __future__ import annotations

from typing import Final

# Nine-area operator console action IDs.
MAIN_BACKUP = "main_backup"
MAIN_SYNC = "main_sync"
MAIN_REPO = "main_repo"
MAIN_STORAGE = "hdd_storage"
MAIN_RECOVERY = "main_recovery"
MAIN_MIGRATION = "main_migration"
MAIN_DEPLOY = "main_deploy_handoff"
MAIN_REPORTS = "reports_history"
MAIN_HEALTH = "main_health"

# Compatibility aliases (older call sites / recommendations).
MAIN_BACKUP_SYNC = MAIN_BACKUP  # guided backup remains the writer-ready recommendation
ACTION_HDD_STORAGE = MAIN_STORAGE
ACTION_BACKUP = MAIN_BACKUP
ACTION_BACKUP_LEGACY = "backup_sources"
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
# Obsolete Advanced hub id — maps to Backup and verification.
MAIN_ADVANCED = "main_advanced"

# (key, title, action_id, requires_hdd_writes)
MAIN_MENU_OPTIONS: Final[list[tuple[str, str, str, bool]]] = [
    ("1", "Backup and verification", MAIN_BACKUP, True),
    ("2", "Database sync and data movement", MAIN_SYNC, False),
    ("3", "Git and repository recovery", MAIN_REPO, False),
    ("4", "Mercury HDD and storage", MAIN_STORAGE, False),
    ("5", "Restore and disaster recovery", MAIN_RECOVERY, False),
    ("6", "Workstation migration", MAIN_MIGRATION, False),
    ("7", "Deployment and handoff", MAIN_DEPLOY, False),
    ("8", "Reports, evidence, and history", MAIN_REPORTS, False),
    ("9", "System health and configuration", MAIN_HEALTH, False),
]

# Software-only console when the Mercury HDD is absent (planning / reconnect).
SOFTWARE_ONLY_MENU_OPTIONS: Final[list[tuple[str, str, str, bool]]] = [
    ("1", "Reconnect or configure Mercury HDD", MAIN_STORAGE, False),
    ("2", "Restore and disaster recovery planning", MAIN_RECOVERY, False),
    ("3", "Git and repository recovery (planning)", MAIN_REPO, False),
    ("4", "Reports available on this host", MAIN_REPORTS, False),
    ("5", "System health and configuration", MAIN_HEALTH, False),
]

WRITES_DISABLED_SUFFIX = "unavailable · writes disabled"
HDD_ABSENT_SUFFIX = "unavailable"
REPORTS_LIMITED_SUFFIX = "limited · host-local only"


def _active_menu_options(*, software_only: bool = False) -> list[tuple[str, str, str, bool]]:
    return list(SOFTWARE_ONLY_MENU_OPTIONS if software_only else MAIN_MENU_OPTIONS)


def main_menu_option_by_action(
    action_id: str, *, software_only: bool = False
) -> tuple[str, str]:
    # Map legacy expert action ids onto the nine-area console homes.
    legacy_aliases = {
        ACTION_BACKUP_LEGACY: MAIN_BACKUP,
        ACTION_SYNC: MAIN_SYNC,
        ACTION_OFFLINE_REPOS: MAIN_REPO,
        ACTION_ENVIRONMENT: MAIN_HEALTH,
        ACTION_INVENTORY: MAIN_HEALTH,
        ACTION_DOCTOR: MAIN_HEALTH,
        ACTION_DEPLOY: MAIN_DEPLOY,
        ACTION_RECOVERY_LEGACY: MAIN_RECOVERY,
        ACTION_HANDOFF: MAIN_DEPLOY,
        MAIN_ADVANCED: MAIN_BACKUP,
        MAIN_BACKUP_SYNC: MAIN_BACKUP,
        "main_backup_sync": MAIN_BACKUP,
        "system_deployment": MAIN_DEPLOY,
        "workstation_handoff": MAIN_DEPLOY,
        "disaster_recovery": MAIN_RECOVERY,
        "backup_sources": MAIN_BACKUP,
        "sync_prod_dev": MAIN_SYNC,
        "offline_repos": MAIN_REPO,
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
    software_only: bool = False,
    recommended_action_id: str | None = None,
) -> list[tuple[str, str]]:
    """Return ``(key, title)`` pairs for rendering, with availability suffixes."""
    items: list[tuple[str, str]] = []
    for key, title, action, needs_writes in _active_menu_options(software_only=software_only):
        suffix = ""
        # Backup remains selectable while writes are disabled so guided restore can run.
        if (
            needs_writes
            and action != MAIN_BACKUP
            and (not writes_allowed or hdd_detached)
        ):
            if hdd_detached:
                suffix = HDD_ABSENT_SUFFIX
            else:
                suffix = WRITES_DISABLED_SUFFIX
        elif hdd_detached and action == MAIN_REPORTS:
            suffix = REPORTS_LIMITED_SUFFIX
        display = title
        if suffix:
            display = f"{title}  {suffix}"
        if recommended_action_id and action == recommended_action_id:
            display = f"{display}      recommended"
        items.append((key, display))
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
