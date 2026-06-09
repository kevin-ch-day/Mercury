"""Display sync readiness report."""

from mercury import output
from mercury.database.core import shared_authority_note
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.sync.readiness import SyncReadinessReport


def _display_sync_database_name(database: str) -> str:
    if database.endswith("_prod"):
        return database[: -len("_prod")]
    if database.endswith("_dev"):
        return database[: -len("_dev")]
    return database


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
        display_screen.write_fields({"Backup root": report.backup_root})
        rows: list[list[str]] = []
        for entry in report.entries:
            database = _display_sync_database_name(entry.prod)
            status = "ready" if entry.ready_for_sync_planning else "blocked"
            reason = _compact_readiness_reason(
                ready=entry.ready_for_sync_planning,
                blockers=entry.blockers,
            )
            rows.append([database, status, reason])
        display_screen.write_blank()
        table = Table.from_headers(
            ["DATABASE", "STATUS", "REASON"],
            rows,
            style=TableStyle(indent=0),
            min_col_widths=[24, 8, 24],
            max_col_widths=[28, 12, 40],
        )
        display_screen.write_structured_table(table)
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
