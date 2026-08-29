"""Operator display for source dumpability."""

from __future__ import annotations

from mercury import output
from mercury.backup.dump_preflight import SourcesDumpability
from mercury.terminal import screen as display_screen


def print_dumpability_report(report: SourcesDumpability, *, menu: bool = False) -> None:
    """Print dumpability for backup sources (read-only inspection)."""
    if not report.entries:
        message = "Dumpability not checked (no MariaDB connection)."
        if menu:
            display_screen.write_summary(message)
        else:
            output.write(message)
        return
    if not report.blocked:
        message = f"Dumpability OK · {len(report.entries)} source(s) can SHOW CREATE TABLE"
        if menu:
            display_screen.write_summary(message)
        else:
            output.write(message)
        return
    if not menu:
        output.heading("Dumpability")
        output.write(
            f"{len(report.blocked)} of {len(report.entries)} source(s) blocked"
            f" · {report.dumpable_count} dumpable"
        )
    else:
        display_screen.write_summary(
            f"{len(report.blocked)} of {len(report.entries)} source(s) blocked"
            f" · {report.dumpable_count} dumpable"
        )
    for line in report.issue_lines():
        if menu:
            display_screen.write_status("fail", line)
        else:
            output.bullet(line)
    hint = report.operator_repair_hint()
    if menu:
        display_screen.write_summary(hint)
    else:
        output.write(hint)
