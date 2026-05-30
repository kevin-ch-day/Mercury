"""Plain-text formatting for database inventory."""

from mercury import output
from mercury.database.core import (
    CATALOG_BY_NAME,
    DatabaseInventory,
    classify_database,
    format_entry_line,
    inventory_summary,
    is_live_inventory,
)
from mercury.database.mariadb.probe import ReadOnlyDiscoveryPlan, ToolingProbe


def print_inventory(inventory: DatabaseInventory) -> None:
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
        output.item(format_entry_line(entry))

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
    if entry:
        output.field("project", entry.project)
        output.field("description", entry.description)
    output.field("role", c.role.value)
    output.field("backup_source", c.backup_source)
    output.field("dev_target", c.dev_target)
    output.field("manual_review", c.manual_review)
    output.field("notes", c.notes)
