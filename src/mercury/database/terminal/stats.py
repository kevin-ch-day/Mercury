"""Display batch database size statistics."""

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.database.mariadb.stats import DatabaseStatsReport


def print_database_stats(report: DatabaseStatsReport, *, compact: bool = False) -> None:
    if compact:
        display_screen.write_section("Database sizes")
        display_screen.write_fields(
            {
                "databases": len(report.databases),
                "total": display_format.format_bytes(report.total_bytes),
            }
        )
        rows = [
            [entry.name, str(entry.table_count), display_format.format_bytes(entry.total_bytes)]
            for entry in report.databases
        ]
        display_screen.write_table(["DATABASE", "TABLES", "SIZE"], rows)
        for note in report.notes:
            display_screen.write_status("info", note)
        return

    from mercury.terminal.screen import write_report_header

    write_report_header("DATABASE SIZES (read-only)")
    output.field("access_mode", report.access_mode)
    output.field("database_count", len(report.databases))
    output.field("total_bytes", report.total_bytes)
    output.field("total_size", display_format.format_bytes(report.total_bytes))
    output.heading("Per database")
    for entry in report.databases:
        output.item(
            f"{entry.name}: tables={entry.table_count}, views={entry.view_count}, "
            f"{display_format.format_bytes(entry.total_bytes)}"
        )
    for note in report.notes:
        output.bullet(note)
