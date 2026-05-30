"""Display MariaDB server probe results."""

from mercury import output
from mercury.terminal import screen as display_screen
from mercury.database.mariadb.session import MariaDbServerProbe


def print_server_probe(probe: MariaDbServerProbe, *, compact: bool = False) -> None:
    if compact:
        _print_server_probe_compact(probe)
        return

    output.heading("MariaDB server probe (read-only)")
    output.field("host", probe.host)
    output.field("port", probe.port)
    output.field("configured_user", probe.configured_user)
    output.field("connected", probe.connected)
    output.field("read_only", probe.read_only)
    output.field("driver", probe.driver)
    if probe.unix_socket:
        output.field("unix_socket", probe.unix_socket)
    if probe.config_path:
        output.field("config", probe.config_path)

    if probe.connected:
        output.field("latency_ms", probe.latency_ms)
        output.field("server_version", probe.server_version or "unknown")
        output.field("current_user", probe.current_user or "unknown")
        output.field("user_database_count", probe.user_database_count)
        if probe.sample_databases:
            output.heading("Sample databases")
            for name in probe.sample_databases:
                output.item(name)
            remaining = (probe.user_database_count or 0) - len(probe.sample_databases)
            if remaining > 0:
                output.write(f"  ... and {remaining} more (run: mercury db discover)")

        output.heading("SQL executed (read-only)")
        for sql in probe.sql_executed:
            output.item(sql)
    elif probe.error:
        output.field("error", probe.error)

    if probe.notes:
        output.heading("Notes")
        for note in probe.notes:
            output.bullet(note)

    if probe.connected:
        output.write()
        output.write(
            "Server contacted successfully. No backups, restores, or writes were performed."
        )


def _print_server_probe_compact(probe: MariaDbServerProbe) -> None:
    if not probe.connected:
        display_screen.write_fields({"connected": False})
        if probe.error:
            display_screen.write_status("fail", probe.error)
        return

    from mercury.env.terminal.check import connection_label

    display_screen.write_fields(
        {
            "connected": connection_label(probe),
            "version": probe.server_version or "unknown",
            "latency_ms": probe.latency_ms,
            "databases": probe.user_database_count,
        }
    )
