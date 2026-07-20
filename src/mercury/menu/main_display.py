"""
Menu-specific layout and status line.

Generic formatting lives in ``display_format``; screen writers in
``display_screen``. This module holds menu constants and the main menu render.
"""

from __future__ import annotations

from dataclasses import dataclass

from mercury.core.environment_status import build_environment_status
from mercury.core.runtime import should_probe_database_status
from mercury.terminal.format import format_bytes, short_path
from mercury.terminal.theme import (
    body_label,
    dashboard_panel,
    help_line,
    menu_bottom_option as theme_menu_bottom_option,
    menu_header_lines,
    menu_item_line,
    menu_status_row,
    rule_line,
)
from mercury.menu.dashboard import dashboard_rows
from mercury.terminal.screen import (
    open_screen,
    write_blank,
    write_bullets,
    write_fields,
    write_hint,
    write_list,
    write_section,
    write_status,
    write_summary,
    write_table,
)
from mercury.core.runtime import operator_status

# Re-export shared helpers so existing imports keep working.
__all__ = [
    "MENU_FOOTER",
    "MENU_SECTIONS",
    "MENU_SUBTITLE",
    "MENU_TITLE",
    "MENU_EXIT_LABEL",
    "MENU_ITEM_INDENT",
    "MENU_RETURN_LABEL",
    "MenuItem",
    "dashboard_rows",
    "format_bytes",
    "format_menu_bottom_option",
    "menu_item",
    "menu_items_by_key",
    "open_screen",
    "readiness_summary",
    "render_main_menu",
    "render_main_menu_body",
    "render_menu_help",
    "render_option_menu",
    "short_path",
    "status_line",
    "status_rows",
    "write_blank",
    "write_bullets",
    "write_fields",
    "write_hint",
    "write_list",
    "write_section",
    "write_status",
    "write_summary",
    "write_table",
]

MENU_TITLE = "MERCURY OPERATOR CONSOLE"
MENU_SUBTITLE = "Database Backup, Sync, and Disaster Recovery Utility"
MENU_FOOTER = "[0] Exit"
MENU_ITEM_INDENT = "      "
MENU_EXIT_LABEL = "Exit"
MENU_RETURN_LABEL = "Return"


@dataclass(frozen=True)
class MenuItem:
    key: str
    title: str


MENU_SECTIONS: list[tuple[str, list[MenuItem]]] = [
    (
        "Core workflows",
        [
            MenuItem("1", "Backup source databases"),
            MenuItem("2", "Sync production to development"),
            MenuItem("3", "Reports and backup history"),
            MenuItem("4", "Sync Offline GitHub Repositories"),
            MenuItem("5", "Environment details"),
            MenuItem("6", "Database inventory"),
            MenuItem("7", "System doctor / repair guide"),
            MenuItem("8", "System Deployment"),
            MenuItem("9", "Disaster Recovery"),
            MenuItem("10", "Workstation handoff"),
        ],
    ),
]


def menu_items_by_key() -> dict[str, MenuItem]:
    return {item.key: item for _section, items in MENU_SECTIONS for item in items}


def menu_item(key: str) -> MenuItem | None:
    return menu_items_by_key().get(key)


def iter_menu_items() -> list[MenuItem]:
    return [item for _section, items in MENU_SECTIONS for item in items]


def _status_tags(*, probe_database: bool | None = None) -> tuple[str, str, str, str, str, str]:
    probe = should_probe_database_status() if probe_database is None else probe_database
    status = operator_status(probe_database=probe)
    env = build_environment_status(probe_database=probe)
    connected = env.mariadb.connection_works is True
    safety_tag = "[--]" if "dry-run" in status["safety"] else "[ok]"
    db_tag = "[ok]" if connected else "[!!]"
    if connected:
        database_label = status["database"]
    elif not env.config.local_toml_present:
        database_label = "config missing"
    elif env.mariadb.connection_works is False:
        database_label = "credentials failed"
    elif env.mariadb.mariadb_client_found and env.mariadb.service_active:
        database_label = "config missing" if not env.mariadb.config_present else "not connected"
    else:
        database_label = status["database"]
    backup_root = status["backup_root"]
    backup_tag = "[!!]" if env.policy.backup_root_state() != "usb-mounted" else "[ok]"
    backup_detail = short_path(backup_root, max_len=40) if backup_root != "not configured" else "not configured"
    return (
        safety_tag,
        status["safety"],
        db_tag,
        database_label,
        backup_tag,
        backup_detail,
    )


def readiness_summary(*, probe_database: bool | None = None) -> str:
    """One-line readiness headline (verbose views)."""
    probe = should_probe_database_status() if probe_database is None else probe_database
    env = build_environment_status(probe_database=probe)
    safety_tag, _safety, db_tag, _database, backup_tag, backup_root = _status_tags(
        probe_database=probe_database
    )
    if backup_root == "not configured" or not env.config.local_toml_present:
        return menu_status_row("Attention", "[!!]", "Local config not initialized")
    if db_tag == "[!!]":
        return menu_status_row("Attention", "[!!]", "MariaDB not reachable")
    if safety_tag == "[--]":
        return menu_status_row("Ready", "[--]", "Dry-run mode")
    return menu_status_row("Ready", "[ok]", "Live actions enabled")


def status_rows(*, probe_database: bool | None = None) -> list[str]:
    """Detailed operator status (verbose views)."""
    safety_tag, safety, db_tag, database, backup_tag, backup_root = _status_tags(
        probe_database=probe_database
    )
    return [
        menu_status_row("Mode", safety_tag, safety),
        menu_status_row("Database", db_tag, database),
        menu_status_row("Backups", backup_tag, backup_root),
    ]


def status_line(*, probe_database: bool | None = None) -> str:
    """Single-line status (legacy callers and compact views)."""
    safety_tag, safety, db_tag, database, backup_tag, backup_root = _status_tags(
        probe_database=probe_database
    )
    return (
        f"Status: {safety_tag} {safety} | "
        f"DB: {db_tag} {database} | "
        f"Backups: {backup_tag} {backup_root}"
    )


def format_menu_bottom_option(label: str) -> str:
    """Bottom row for any Mercury menu — ``[0] Exit`` or ``[0] Return``."""
    return theme_menu_bottom_option(label, indent=len(MENU_ITEM_INDENT))


def render_option_menu(
    *,
    title: str | None = None,
    options: list[tuple[str, str]],
    bottom_label: str = MENU_RETURN_LABEL,
) -> str:
    """Render a numbered menu with ``[0]`` exit/return as the last option."""
    lines: list[str] = []
    if title:
        lines.extend([title, ""])
    for key, option_label in options:
        lines.append(menu_item_line(key, option_label, indent=len(MENU_ITEM_INDENT)))
    lines.append(format_menu_bottom_option(bottom_label))
    return "\n".join(lines)


def _sectioned_menu_item_lines() -> list[str]:
    lines: list[str] = []
    for item in iter_menu_items():
        lines.append(
            menu_item_line(
                item.key,
                item.title,
                indent=len(MENU_ITEM_INDENT),
            )
        )
    lines.append(format_menu_bottom_option(MENU_EXIT_LABEL))
    return lines


def _flat_menu_item_lines() -> list[str]:
    return _sectioned_menu_item_lines()


def render_menu_help() -> str:
    """Compact on-demand help for menu shortcuts."""
    rule = rule_line()
    keys = [item.key for item in iter_menu_items()]
    key_label = f"{keys[0]}-{keys[-1]}" if keys else "1-9"
    lines = [
        rule,
        body_label("Operator console help"),
        help_line(f"Enter {key_label} for actions, 0 or q to exit."),
        help_line("Shortcut: h opens workstation handoff (menu 9)."),
        help_line("Handoff: menu 9 [2] guided wizard · [11] receiver guide · ./run.sh transfer receive"),
        help_line("Recovery: menu 8 for DR status; complete handoff media uses transfer receive on receiver."),
        "",
        help_line("For full detail, run the matching CLI command (e.g. ./run.sh db discover)."),
        rule,
    ]
    return "\n".join(lines)


def _main_menu_body_lines(*, probe_database: bool | None = None) -> list[str]:
    rule = rule_line()
    lines = [
        "Main Menu",
        rule,
        *dashboard_panel(dashboard_rows(probe_database=probe_database)),
        rule,
        *_flat_menu_item_lines(),
    ]
    from mercury.repair.startup import main_menu_usb_repair_hint

    hint = main_menu_usb_repair_hint()
    if hint:
        lines.extend(["", hint])
    lines.append("")
    return lines


def render_main_menu_body(*, probe_database: bool | None = None) -> str:
    """Dashboard and options — shown again after each menu action."""
    return "\n".join(_main_menu_body_lines(probe_database=probe_database))


def render_main_menu(*, probe_database: bool | None = None) -> str:
    probe = should_probe_database_status() if probe_database is None else probe_database
    lines = [
        MENU_TITLE,
        MENU_SUBTITLE,
        "",
    ]
    lines.extend(_main_menu_body_lines(probe_database=probe))
    return "\n".join(lines)
