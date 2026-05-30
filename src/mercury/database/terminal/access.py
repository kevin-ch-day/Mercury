"""Display platform database access report."""

from mercury import output
from mercury.database.mariadb.access import PlatformAccessReport


def print_platform_access(report: PlatformAccessReport, *, compact: bool = False) -> None:
    output.heading("Platform database access")
    if compact:
        present = sum(1 for r in report.records if r.on_server)
        missing = len(report.records) - present
        output.field("on_server", present)
        output.field("missing", missing)
        for record in report.records:
            marker = "+" if record.on_server else "-"
            project = f" [{record.project}]" if record.project else ""
            if record.on_server:
                output.item(f"{marker} {record.name}{project}")
            else:
                output.item(f"{marker} {record.name}{project} — {record.status}")
        if report.unexpected_on_server:
            output.write("Non-catalog on server:")
            for name in report.unexpected_on_server:
                output.item(name)
        return

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

    if not compact:
        output.heading("Notes")
        for note in report.notes:
            output.bullet(note)
        output.write()
        output.write("Read-only: live discovery + catalog comparison. No writes performed.")
