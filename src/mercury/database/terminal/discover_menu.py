"""Compact database discovery display for the Mercury menu."""

from __future__ import annotations

from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.database.core import (
    DatabaseInventory,
    filter_inventory,
    role_env_label,
    sort_entries_for_display,
    sync_role_label,
)
from mercury.database.core.scope import is_in_scope


def build_discover_menu_fields(
    inventory: DatabaseInventory,
    *,
    size_by_name: dict[str, int] | None = None,
) -> dict[str, object]:
    """Flat summary fields for menu option 2 — no connection boilerplate."""
    scoped_inventory = filter_inventory(inventory)
    out_of_scope = inventory.count - scoped_inventory.count
    backup_sources = sum(1 for entry in scoped_inventory.entries if entry.backup_source)
    sync_targets = sum(1 for entry in scoped_inventory.entries if entry.dev_target)
    fields: dict[str, object] = {
        "Active scope": scoped_inventory.count,
        "Backup sources": backup_sources,
        "Sync targets": sync_targets,
    }
    if size_by_name:
        total = sum(size_by_name.get(entry.name, 0) for entry in scoped_inventory.entries)
        fields["Total size"] = display_format.format_bytes(total)
    if out_of_scope:
        fields["Out of scope"] = f"{out_of_scope} ignored"
    return fields

def _discover_menu_table_rows(inventory: DatabaseInventory) -> tuple[list[str], list[list[str]]]:
    headers = ["DATABASE", "ROLE", "BACKUP", "SYNC ROLE"]
    rows: list[list[str]] = []
    for entry in sort_entries_for_display(filter_inventory(inventory).entries):
        rows.append(
            [
                entry.name,
                role_env_label(entry.role),
                display_format.format_yes_no(entry.backup_source),
                sync_role_label(entry.name),
            ]
        )
    return headers, rows


def print_discover_menu(
    inventory: DatabaseInventory,
    *,
    size_by_name: dict[str, int] | None = None,
) -> None:
    out_of_scope_names = [
        entry.name for entry in sort_entries_for_display(inventory.entries) if not is_in_scope(entry.name)
    ]
    display_screen.write_fields(
        build_discover_menu_fields(inventory, size_by_name=size_by_name),
    )
    display_screen.write_blank()
    headers, rows = _discover_menu_table_rows(inventory)
    display_screen.write_compact_table(
        headers,
        rows,
        min_col_widths=[28, 8, 8, 12],
        max_col_widths=[36, 8, 8, 12],
    )
    display_screen.write_blank()
    display_screen.write_summary("Shared authority: android_permission_intel (backup-only)")
    if out_of_scope_names:
        display_screen.write_blank()
        display_screen.write_summary(
            f"Ignored databases: {len(out_of_scope_names)}"
        )
        display_screen.write_summary(", ".join(out_of_scope_names))
