"""Display log status and analysis for CLI."""

from __future__ import annotations

from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.logging.analysis import LogSession, LogStatusReport


def print_log_status(report: LogStatusReport) -> None:
    display_screen.write_section("Log status")
    display_screen.write_fields(
        {
            "log_dir": report.log_dir,
            "logging_enabled": report.logging_enabled,
            "daily_log_errors": report.total_errors,
            "daily_log_warnings": report.total_warnings,
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
        print_session_table(report.sessions)


def print_session_table(sessions: list[LogSession]) -> None:
    rows = []
    for session in sessions:
        command = session.command or "(unknown)"
        if len(command) > 48:
            command = f"…{command[-47:]}"
        if not session.start_line:
            status = f"unpaired {session.exit_code}"
        elif session.ambiguous:
            status = "ambiguous"
        elif session.exit_code is None:
            status = "no end record"
        else:
            status = str(session.exit_code)
        elapsed = "" if session.elapsed_ms is None else f"{session.elapsed_ms:.1f}"
        rows.append([session.session_id, command, status, elapsed])
    display_screen.write_table(["SESSION", "COMMAND", "EXIT", "MS"], rows)
