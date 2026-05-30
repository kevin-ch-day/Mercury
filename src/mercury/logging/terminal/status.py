"""Display log status and analysis for CLI."""

from __future__ import annotations

from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.logging.analysis import LogStatusReport


def print_log_status(report: LogStatusReport) -> None:
    display_screen.write_section("Log status")
    display_screen.write_fields(
        {
            "log_dir": report.log_dir,
            "logging_enabled": report.logging_enabled,
            "total_errors": report.total_errors,
            "total_warnings": report.total_warnings,
        }
    )
    if not report.files:
        display_screen.write_status("warn", "No log files yet — run any mercury command.")
        return

    rows = []
    for info in report.files:
        rows.append(
            [
                info.name,
                display_format.format_bytes(info.size_bytes),
                str(info.lines),
                str(info.errors),
                str(info.warnings),
            ]
        )
    display_screen.write_table(["FILE", "SIZE", "LINES", "ERRORS", "WARN"], rows)

    if report.sessions:
        display_screen.write_section("Recent sessions")
        session_rows = []
        for session in report.sessions:
            command = session.command or "(unknown)"
            if len(command) > 48:
                command = f"…{command[-47:]}"
            exit_code = "" if session.exit_code is None else str(session.exit_code)
            session_rows.append([session.session_id, command, exit_code])
        display_screen.write_table(["SESSION", "COMMAND", "EXIT"], session_rows)
