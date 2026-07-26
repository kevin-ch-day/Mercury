"""Backup Operations menu option definitions (single source of truth)."""

from __future__ import annotations

from typing import Final

# Stable action ids used by completion hints and tests.
ACTION_BACKUP_SYNC_SESSION = "backup_sync_session"
ACTION_FULL_BACKUP = "full_backup"
ACTION_PRODUCTION_BACKUP = "production_backup"
ACTION_VERIFY = "verify_sources"
ACTION_RESTORE_CHECK = "restore_check"
ACTION_BUNDLE = "write_bundle"
ACTION_PREVIEW = "preview_plan"
ACTION_HANDOFF = "open_handoff"
ACTION_DEV_BACKUP = "development_backup"
# Backward-compatible alias (Refresh removed from primary slots in Phase 2).
ACTION_REFRESH = "refresh"

# Backup Ops is backup/verify only. Restore, bundle, and handoff live elsewhere.
BACKUP_MENU_OPTIONS: Final[list[tuple[str, str, str, str]]] = [
    (
        "1",
        "Guided backup session",
        ACTION_BACKUP_SYNC_SESSION,
        "Guided production backup and verify; optional development backup. "
        "Sync and Git lanes remain under their own Main Menu areas.",
    ),
    (
        "2",
        "Run full database backup",
        ACTION_FULL_BACKUP,
        "Back up all configured production databases, verify those newly written "
        "backups, then optionally back up and verify development databases.",
    ),
    (
        "3",
        "Back up production databases",
        ACTION_PRODUCTION_BACKUP,
        "Production-only backup workflow (does not include development databases).",
    ),
    (
        "4",
        "Back up development databases",
        ACTION_DEV_BACKUP,
        "Development-only optional recovery workflow (not the default handoff package).",
    ),
    (
        "5",
        "Verify source backups",
        ACTION_VERIFY,
        "Verify on-disk production/shared backup artifacts and stamp manifests.",
    ),
    (
        "6",
        "Preview backup plan",
        ACTION_PREVIEW,
        "Dry-run production backup plan.",
    ),
]

# Next-step hints that point at other Main Menu homes (not Backup Ops slots).
CROSS_AREA_NEXT_HINTS: Final[dict[str, tuple[str, str]]] = {
    ACTION_RESTORE_CHECK: ("5", "Restore and disaster recovery"),
    ACTION_BUNDLE: ("7", "Deployment and handoff"),
    ACTION_HANDOFF: ("7", "Deployment and handoff"),
}

# Actions that write under the Mercury HDD (or mutate manifests).
BACKUP_MENU_WRITE_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        ACTION_BACKUP_SYNC_SESSION,
        ACTION_FULL_BACKUP,
        ACTION_PRODUCTION_BACKUP,
        ACTION_VERIFY,  # stamps manifests when update_manifest=True
        ACTION_DEV_BACKUP,
    }
)

DETACH_UNAVAILABLE_SUFFIX = "unavailable · Mercury writes disabled"


def backup_menu_render_options(
    *,
    writes_allowed: bool = True,
    recommend_guided: bool = False,
) -> list[tuple[str, str]]:
    """Options for ``render_submenu``."""
    options: list[tuple[str, str]] = []
    for key, label, action_id, _help in BACKUP_MENU_OPTIONS:
        display = label
        if recommend_guided and action_id == ACTION_BACKUP_SYNC_SESSION:
            display = f"{label}      recommended"
        # Session remains selectable while writes are disabled so guided restore can run.
        if (
            not writes_allowed
            and action_id in BACKUP_MENU_WRITE_ACTIONS
            and action_id != ACTION_BACKUP_SYNC_SESSION
        ):
            options.append((key, f"{display}  {DETACH_UNAVAILABLE_SUFFIX}"))
        else:
            options.append((key, display))
    return options


def backup_menu_option_by_action(action_id: str) -> tuple[str, str]:
    """Return ``(key, label)`` for a stable action id."""
    for key, label, action, _help in BACKUP_MENU_OPTIONS:
        if action == action_id:
            return key, label
    if action_id in CROSS_AREA_NEXT_HINTS:
        key, label = CROSS_AREA_NEXT_HINTS[action_id]
        return key, label
    raise KeyError(f"Unknown backup menu action: {action_id}")


def backup_menu_hint(action_id: str) -> str:
    """Operator hint synchronized with menu numbering (or Main Menu home)."""
    key, label = backup_menu_option_by_action(action_id)
    if action_id in CROSS_AREA_NEXT_HINTS:
        return f"{label} [{key}]"
    return f"{label} [{key}]"


def backup_menu_next_actions(*action_ids: str) -> list[str]:
    return [backup_menu_hint(action_id) for action_id in action_ids]
