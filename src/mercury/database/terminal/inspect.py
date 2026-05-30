"""Display database inspection results."""

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.database.mariadb.inspect import DatabaseInspectResult


def print_database_inspect(result: DatabaseInspectResult) -> None:
    output.heading("DATABASE INSPECT (read-only)")
    output.field("name", result.name)
    output.field("role", result.role)
    output.field("backup_source", result.backup_source)
    output.field("exists_on_server", result.exists_on_server)
    output.field("connected", result.connected)
    output.field("access_mode", result.access_mode)
    if result.table_count is not None:
        output.field("table_count", result.table_count)
    if result.view_count is not None:
        output.field("view_count", result.view_count)
    if result.total_bytes is not None:
        output.field("total_bytes", result.total_bytes)
    if result.error:
        output.field("error", result.error)
    for note in result.notes:
        output.bullet(note)


def print_database_inspect_menu(result: DatabaseInspectResult) -> None:
    """Compact inspect output for the interactive menu."""
    fields: dict[str, object] = {
        "name": result.name,
        "role": result.role,
        "backup_source": display_format.format_yes_no(result.backup_source),
        "exists_on_server": display_format.format_yes_no(result.exists_on_server),
    }
    if result.table_count is not None:
        fields["tables"] = result.table_count
    if result.view_count is not None:
        fields["views"] = result.view_count
    if result.total_bytes is not None:
        fields["size"] = display_format.format_bytes(result.total_bytes)
    if result.error:
        fields["error"] = result.error
    display_screen.write_fields(fields)
    for note in result.notes:
        display_screen.write_summary(note)
