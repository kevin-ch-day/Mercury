"""Compact database discovery display for the Mercury menu."""

from __future__ import annotations

from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.database.core import (
    DatabaseInventory,
    inventory_role_summary,
    inventory_summary,
    role_env_label,
    sort_entries_for_display,
)


def build_discover_menu_fields(
    inventory: DatabaseInventory,
    *,
    size_by_name: dict[str, int] | None = None,
) -> dict[str, object]:
    """Flat summary fields for menu option 2 — no connection boilerplate."""
    fields: dict[str, object] = {
        "databases": inventory.count,
    }
    summary = inventory_summary(inventory)
    if summary:
        fields["roles"] = inventory_role_summary(summary)
    if size_by_name:
        total = sum(size_by_name.get(entry.name, 0) for entry in inventory.entries)
        fields["total_size"] = display_format.format_bytes(total)
    return fields


def _discover_menu_table_rows(inventory: DatabaseInventory) -> tuple[list[str], list[list[str]]]:
    headers = ["DATABASE", "ENV", "BACKUP"]
    rows: list[list[str]] = []
    for entry in sort_entries_for_display(inventory.entries):
        rows.append(
            [
                entry.name,
                role_env_label(entry.role),
                display_format.format_yes_no(entry.backup_source),
            ]
        )
    return headers, rows


def print_discover_menu(
    inventory: DatabaseInventory,
    *,
    size_by_name: dict[str, int] | None = None,
) -> None:
    display_screen.write_fields(
        build_discover_menu_fields(inventory, size_by_name=size_by_name),
    )
    display_screen.write_blank()
    headers, rows = _discover_menu_table_rows(inventory)
    display_screen.write_table(headers, rows, max_col_widths=[36, 8, 8])
