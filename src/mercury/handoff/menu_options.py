"""Handoff / migration-package menu option definitions (symbolic IDs)."""

from __future__ import annotations

from typing import Final

# Primary handoff menu
ACTION_REFRESH = "handoff_refresh"
ACTION_BUILD_PACKAGE = "handoff_build_package"
ACTION_CAPTURE_WORKTREES = "handoff_capture_worktrees"
ACTION_RECEIVER_GUIDE = "handoff_receiver_guide"
ACTION_TOOLS = "handoff_tools"

# Handoff Tools submenu
ACTION_TOOLS_RESUME = "handoff_tools_resume"
ACTION_TOOLS_BACKUP = "handoff_tools_backup"
ACTION_TOOLS_VERIFY = "handoff_tools_verify"
ACTION_TOOLS_REPO = "handoff_tools_repo_bundles"
ACTION_TOOLS_DB = "handoff_tools_db_bundle"
ACTION_TOOLS_TRANSFER = "handoff_tools_transfer"
ACTION_TOOLS_CHECKLIST = "handoff_tools_checklist"
ACTION_TOOLS_HISTORY = "handoff_tools_history"

HANDOFF_MENU_OPTIONS: Final[list[tuple[str, str, str]]] = [
    ("1", "Refresh status", ACTION_REFRESH),
    ("2", "Build Migration Package", ACTION_BUILD_PACKAGE),
    ("3", "Capture Web Worktrees", ACTION_CAPTURE_WORKTREES),
    ("4", "Receiver Guide", ACTION_RECEIVER_GUIDE),
    ("5", "Handoff Tools", ACTION_TOOLS),
]

HANDOFF_TOOLS_OPTIONS: Final[list[tuple[str, str, str]]] = [
    ("1", "Resume Package Build", ACTION_TOOLS_RESUME),
    ("2", "Run Backup", ACTION_TOOLS_BACKUP),
    ("3", "Verify Backups", ACTION_TOOLS_VERIFY),
    ("4", "Create Repository Bundles", ACTION_TOOLS_REPO),
    ("5", "Create Database Bundle", ACTION_TOOLS_DB),
    ("6", "Create Transfer Package", ACTION_TOOLS_TRANSFER),
    ("7", "Review Checklist", ACTION_TOOLS_CHECKLIST),
    ("8", "Handoff History", ACTION_TOOLS_HISTORY),
]


def handoff_menu_option_by_action(action_id: str) -> tuple[str, str]:
    for key, label, action in HANDOFF_MENU_OPTIONS:
        if action == action_id:
            return key, label
    raise KeyError(f"Unknown handoff menu action: {action_id}")


def handoff_tools_option_by_action(action_id: str) -> tuple[str, str]:
    for key, label, action in HANDOFF_TOOLS_OPTIONS:
        if action == action_id:
            return key, label
    raise KeyError(f"Unknown handoff tools action: {action_id}")


def handoff_menu_hint(action_id: str) -> str:
    key, label = handoff_menu_option_by_action(action_id)
    return f"{label} [{key}]"


def handoff_tools_hint(action_id: str) -> str:
    key, label = handoff_tools_option_by_action(action_id)
    return f"{label} [{key}]"


def handoff_nested_hint(tools_action_id: str) -> str:
    """Handoff Tools entry plus a tools-submenu action (registry-resolved numbers)."""
    return f"{handoff_menu_hint(ACTION_TOOLS)} → {handoff_tools_hint(tools_action_id)}"


def handoff_menu_render_options(*, writes_allowed: bool = True) -> list[tuple[str, str]]:
    from mercury.backup.menu_options import DETACH_UNAVAILABLE_SUFFIX

    write_actions = {
        ACTION_BUILD_PACKAGE,
        ACTION_CAPTURE_WORKTREES,
    }
    options: list[tuple[str, str]] = []
    for key, label, action_id in HANDOFF_MENU_OPTIONS:
        if not writes_allowed and action_id in write_actions:
            options.append((key, f"{label}  {DETACH_UNAVAILABLE_SUFFIX}"))
        else:
            options.append((key, label))
    return options


def handoff_tools_render_options(*, writes_allowed: bool = True) -> list[tuple[str, str]]:
    from mercury.backup.menu_options import DETACH_UNAVAILABLE_SUFFIX

    write_actions = {
        ACTION_TOOLS_RESUME,
        ACTION_TOOLS_BACKUP,
        ACTION_TOOLS_VERIFY,
        ACTION_TOOLS_REPO,
        ACTION_TOOLS_DB,
        ACTION_TOOLS_TRANSFER,
    }
    options: list[tuple[str, str]] = []
    for key, label, action_id in HANDOFF_TOOLS_OPTIONS:
        if not writes_allowed and action_id in write_actions:
            options.append((key, f"{label}  {DETACH_UNAVAILABLE_SUFFIX}"))
        else:
            options.append((key, label))
    return options
