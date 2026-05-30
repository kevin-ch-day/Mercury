"""Display platform database access report."""

from mercury import output
from mercury.database.mariadb.access import PlatformAccessReport


def print_platform_access(report: PlatformAccessReport) -> None:
    output.heading("PLATFORM DATABASE ACCESS (read-only)")
    output.field("connection", report.connection)
    output.field("access_mode", report.access_mode)
    output.field("server_database_count", report.server_database_count)

    output.heading("Catalog databases")
    for record in report.records:
        marker = "+" if record.on_server else "-"
        flags = []
        if record.backup_source:
            flags.append("backup_source")
        flag_text = f" ({', '.join(flags)})" if flags else ""
        project = f" [{record.project}]" if record.project else ""
        output.item(f"{marker} {record.name}{project} <{record.role}>{flag_text} — {record.status}")

    if report.unexpected_on_server:
        output.heading("Non-catalog databases on server (manual review)")
        for name in report.unexpected_on_server:
            output.item(name)

    output.heading("Notes")
    for note in report.notes:
        output.bullet(note)

    output.write()
    output.write("Read-only: SHOW DATABASES + catalog comparison. No writes performed.")
