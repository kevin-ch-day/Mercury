"""Plain-text formatting for database inventory."""

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.database.core import (
    CATALOG_BY_NAME,
    DatabaseInventory,
    classify_database,
    format_entry_line,
    inventory_role_summary,
    inventory_summary,
    is_live_inventory,
    role_env_label,
    sort_entries_for_display,
)
from mercury.database.core.models import DatabaseRecord
from mercury.database.mariadb.probe import ReadOnlyDiscoveryPlan, ToolingProbe


def _inventory_table_rows(
    entries: list[DatabaseRecord],
    *,
    size_by_name: dict[str, int] | None = None,
) -> tuple[list[str], list[list[str]]]:
    sorted_entries = sort_entries_for_display(entries)
    headers = ["DATABASE", "ENV", "BACKUP"]
    if size_by_name:
        headers.append("SIZE")
    if any(entry.project for entry in sorted_entries):
        headers.append("PROJECT")
    rows: list[list[str]] = []
    for entry in sorted_entries:
        row = [
            entry.name,
            role_env_label(entry.role),
            display_format.format_yes_no(entry.backup_source),
        ]
        if size_by_name is not None:
            size = size_by_name.get(entry.name)
            row.append(display_format.format_bytes(size) if size is not None else "—")
        if "PROJECT" in headers:
            row.append(entry.project or "")
        rows.append(row)
    return headers, rows


def print_inventory(
    inventory: DatabaseInventory,
    *,
    compact: bool = False,
    size_by_name: dict[str, int] | None = None,
) -> None:
    if compact:
        summary = inventory_summary(inventory)
        summary_text = inventory_role_summary(summary) if summary else "none"
        display_screen.write_summary(f"{inventory.count} on server — {summary_text}")
        if size_by_name:
            total = sum(size_by_name.get(entry.name, 0) for entry in inventory.entries)
            display_screen.write_summary(f"Total size: {display_format.format_bytes(total)}")
        display_screen.write_blank()
        headers, rows = _inventory_table_rows(inventory.entries, size_by_name=size_by_name)
        min_widths = [28, 8, 8]
        if size_by_name:
            min_widths.append(10)
        if "PROJECT" in headers:
            min_widths.append(12)
        display_screen.write_compact_table(headers, rows, min_col_widths=min_widths)
        return

    output.heading("Known databases")
    output.field("connection", inventory.connection)
    output.field("mode", inventory.mode)
    output.field("primary_config", inventory.primary_config or "(catalog only)")
    output.field("count", inventory.count)

    summary = inventory_summary(inventory)
    if summary:
        parts = [f"{role}={count}" for role, count in sorted(summary.items())]
        output.field("by_role", ", ".join(parts))

    output.heading("Databases")
    if not inventory.entries:
        output.item("(none — add config/databases.toml or use: mercury db discover --demo)")
        return

    for entry in inventory.entries:
        output.item(format_entry_line(entry, compact=False))

    output.write()
    if is_live_inventory(inventory):
        output.write(
            "Live read-only discovery (SHOW DATABASES). "
            "No backups, restores, or schema changes were performed."
        )
    else:
        output.write(
            "No database server was contacted. "
            "Names come from config and platform catalog."
        )


def print_readonly_discovery_plan(plan: ReadOnlyDiscoveryPlan, tooling: ToolingProbe) -> None:
    output.heading("MariaDB read-only discovery reference")
    output.field("status", plan.status)
    output.field("live_actions_enabled", plan.live_actions_enabled)

    output.heading("Client tools on PATH")
    for name, path in tooling.tools.items():
        output.field(name, path)

    output.heading("Planned steps")
    for step in plan.planned_steps:
        output.bullet(step)

    output.heading("Planned SQL (read-only)")
    for sql in plan.planned_sql:
        output.item(sql)

    output.heading("Notes")
    for note in plan.notes:
        output.bullet(note)


def print_classification(name: str) -> None:
    """Print classification for a single database name."""
    c = classify_database(name)
    entry = CATALOG_BY_NAME.get(name)
    output.heading("Database classification")
    output.field("name", c.name)
    output.field("env", role_env_label(c.role.value))
    if entry:
        output.field("project", entry.project)
        output.field("description", entry.description)
    output.field("role", c.role.value)
    output.field("backup_source", c.backup_source)
    output.field("dev_target", c.dev_target)
    output.field("manual_review", c.manual_review)
    output.field("notes", c.notes)
