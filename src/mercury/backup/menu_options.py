"""Backup Operations menu option definitions (single source of truth)."""

from __future__ import annotations

from typing import Final

# Stable action ids used by completion hints and tests.
ACTION_REFRESH = "refresh"
ACTION_FULL_BACKUP = "full_backup"
ACTION_PRODUCTION_BACKUP = "production_backup"
ACTION_VERIFY = "verify_sources"
ACTION_RESTORE_CHECK = "restore_check"
ACTION_BUNDLE = "write_bundle"
ACTION_PREVIEW = "preview_plan"
ACTION_HANDOFF = "open_handoff"
ACTION_DEV_BACKUP = "development_backup"

# (key, label, action_id, help blurb shown in docs / extended summaries)
BACKUP_MENU_OPTIONS: Final[list[tuple[str, str, str, str]]] = [
    ("1", "Refresh", ACTION_REFRESH, "Reload backup status from operator storage."),
    (
        "2",
        "Run full backup now",
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
        "Verify source backups",
        ACTION_VERIFY,
        "Verify on-disk production/shared backup artifacts and stamp manifests.",
    ),
    (
        "5",
        "Restore-check source backups",
        ACTION_RESTORE_CHECK,
        "Restore newly verified backups into disposable _restorecheck_* schemas.",
    ),
    (
        "6",
        "Write DB bundle and runbooks",
        ACTION_BUNDLE,
        "Write handoff package members from verified backups.",
    ),
    ("7", "Preview backup plan", ACTION_PREVIEW, "Dry-run production backup plan."),
    ("8", "Open workstation handoff", ACTION_HANDOFF, "Open the workstation handoff menu."),
    (
        "9",
        "Back up development databases",
        ACTION_DEV_BACKUP,
        "Development-only optional recovery workflow (not the default handoff package).",
    ),
]


def backup_menu_render_options(*, backup_ready: bool = True) -> list[tuple[str, str]]:
    """Options for ``render_submenu``."""
    options: list[tuple[str, str]] = []
    for key, label, action_id, _help in BACKUP_MENU_OPTIONS:
        if action_id == ACTION_FULL_BACKUP and not backup_ready:
            options.append((key, "Run full backup"))
        else:
            options.append((key, label))
    return options


def backup_menu_option_by_action(action_id: str) -> tuple[str, str]:
    """Return ``(key, label)`` for a stable action id."""
    for key, label, action, _help in BACKUP_MENU_OPTIONS:
        if action == action_id:
            return key, label
    raise KeyError(f"Unknown backup menu action: {action_id}")


def backup_menu_hint(action_id: str) -> str:
    """Operator hint that stays synchronized with menu numbering, e.g. ``Verify source backups [4]``."""
    key, label = backup_menu_option_by_action(action_id)
    return f"{label} [{key}]"


def backup_menu_next_actions(*action_ids: str) -> list[str]:
    return [backup_menu_hint(action_id) for action_id in action_ids]
