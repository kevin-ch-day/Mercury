"""Display sync readiness report."""

from mercury import output
from mercury.database.core import shared_authority_note
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.sync.readiness import SyncReadinessReport


def _compact_readiness_status(*, ready: bool, blockers: list[str]) -> str:
    if ready:
        return "ready"
    if "Backup root is repo-local fallback; configure USB-backed backups before sync readiness." in blockers:
        return "USB root required"
    if "No on-disk backup found for production source." in blockers:
        return "missing verified backup"
    if "Latest backup is not verified (manifest/checksum/size/role)." in blockers:
        return "backup not verified"
    if "Latest backup is not a verified full backup." in blockers:
        return "backup not full"
    if blockers:
        return blockers[0]
    return "blocked"


def _compact_readiness_reason(*, ready: bool, blockers: list[str]) -> str:
    if ready:
        return "verified source backup available"
    return _compact_readiness_status(ready=ready, blockers=blockers)


def print_sync_readiness_report(
    report: SyncReadinessReport,
    *,
    compact: bool = False,
    menu: bool = False,
) -> None:
    if compact:
        display_screen.write_fields(
            {
                "Mode": report.mode.upper(),
                "Backup root": report.backup_root,
                "Ready": report.ready_count,
                "Blocked": report.blocked_count,
            }
        )
        display_screen.write_blank()
        display_screen.write_summary(
            f"{report.ready_count} ready, {report.blocked_count} blocked"
        )
        rows: list[list[str]] = []
        include_backup_id = (not menu) and any(entry.backup_id for entry in report.entries)
        for entry in report.entries:
            pair = display_format.format_pair(entry.prod, entry.expected_dev)
            status = "ready" if entry.ready_for_sync_planning else "blocked"
            reason = _compact_readiness_reason(
                ready=entry.ready_for_sync_planning,
                blockers=entry.blockers,
            )
            row = [pair, status, reason]
            if include_backup_id:
                row.append(entry.backup_id or "—")
            rows.append(row)
        display_screen.write_blank()
        pair_width = 54 if menu else 44
        headers = ["PAIR", "STATUS", "REASON"]
        max_widths = [pair_width, 12, 32]
        if include_backup_id:
            headers.append("BACKUP ID")
            max_widths.append(40)
        display_screen.write_compact_table(
            headers,
            rows,
            min_col_widths=[44 if menu else 40, 8, 24],
            max_col_widths=max_widths,
        )
        display_screen.write_blank()
        display_screen.write_summary(shared_authority_note())
        return

    output.heading("SYNC READINESS (verified source backup required)")
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
    output.write(shared_authority_note())
    output.write("Prerequisite: verified full backup before prod→dev sync.")
