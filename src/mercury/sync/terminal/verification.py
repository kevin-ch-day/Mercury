"""Display dev-target verification against prod backup baselines."""

from __future__ import annotations

from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.sync.verification import SyncVerificationReport


def _status_label(status: str) -> str:
    if status == "complete":
        return "match"
    if status == "incomplete":
        return "mismatch"
    if status == "missing_target":
        return "target missing"
    if status == "backup_unavailable":
        return "backup missing"
    if status == "backup_unverified":
        return "backup unverified"
    return "unknown"


def _detail(entry) -> str:
    if entry.blockers:
        return entry.blockers[0]
    if entry.warnings:
        return entry.warnings[0]
    return "prod backup baseline matches dev target"


def print_sync_verification_report(report: SyncVerificationReport, *, compact: bool = False) -> None:
    display_screen.write_fields(
        {
            "Mode": report.mode,
            "Matched": report.complete_count,
            "Mismatched": report.incomplete_count,
            "Unknown": report.unknown_count,
        }
    )
    rows = [
        [
            entry.source,
            entry.target,
            _status_label(entry.status),
            f"{entry.live_objects}/{entry.backup_objects}" if entry.live_objects is not None and entry.backup_objects is not None else "—",
            _detail(entry),
        ]
        for entry in report.entries
    ]
    display_screen.write_blank()
    table = Table.from_headers(
        ["SOURCE", "TARGET", "STATUS", "OBJECTS dev/backup", "DETAIL"],
        rows,
        style=TableStyle(indent=0),
        min_col_widths=[24, 24, 12, 18, 24],
        max_col_widths=[28, 28, 14, 18, 56],
    )
    display_screen.write_structured_table(table)
