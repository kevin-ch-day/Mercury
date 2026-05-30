"""Display database inspection results."""

from mercury import output
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
