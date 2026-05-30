"""Display sync readiness report."""

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.sync.readiness import SyncReadinessReport


def print_sync_readiness_report(
    report: SyncReadinessReport,
    *,
    compact: bool = False,
    menu: bool = False,
) -> None:
    if compact:
        display_screen.write_summary(
            f"{report.ready_count} ready, {report.blocked_count} blocked"
        )
        rows: list[list[str]] = []
        for entry in report.entries:
            pair = display_format.format_pair(entry.prod, entry.expected_dev)
            status = display_format.format_plan_status(
                ready=entry.ready_for_sync_planning,
                blockers=entry.blockers,
            )
            rows.append([pair, entry.project or "—", status])
        display_screen.write_blank()
        pair_width = 58 if menu else 48
        display_screen.write_table(
            ["PAIR", "PROJECT", "STATUS"],
            rows,
            max_col_widths=[pair_width, 16, 44],
        )
        return

    output.heading("SYNC READINESS (verified prod backup required)")
    output.field("mode", report.mode)
    output.field("backup_root", report.backup_root)
    output.field("ready", report.ready_count)
    output.field("blocked", report.blocked_count)

    for entry in report.entries:
        output.write()
        project = f" [{entry.project}]" if entry.project else ""
        status = "READY" if entry.ready_for_sync_planning else "BLOCKED"
        output.write(f"- {entry.prod}{project} -> {entry.expected_dev} [{status}]")
        if entry.latest_backup_dir:
            output.write(f"  latest_backup: {entry.latest_backup_dir}")
        if entry.backup_id:
            output.write(f"  backup_id: {entry.backup_id}")
        output.write(f"  backup_verified: {entry.backup_verified}")
        for blocker in entry.blockers:
            output.write(f"  - {blocker}")

    output.write()
    output.write("Prerequisite: verified full backup before prod→dev sync.")
