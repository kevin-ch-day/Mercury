"""Display sync readiness report."""

from __future__ import annotations

from mercury import output
from mercury.database.core import shared_authority_note
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.sync.readiness import SyncReadinessEntry, SyncReadinessReport


def _display_sync_database_name(database: str) -> str:
    if database.endswith("_prod"):
        return database[: -len("_prod")]
    if database.endswith("_dev"):
        return database[: -len("_dev")]
    return database


def _compact_readiness_status(*, ready: bool, blockers: list[str]) -> str:
    if ready:
        return "ready"
    if "Backup root is repo-local fallback; configure operator-storage backups before sync readiness." in blockers:
        return "Operator storage root required"
    if "No on-disk backup found for production source." in blockers:
        return "missing backup"
    if "Latest backup is not artifact-verified (manifest/checksum/size/role)." in blockers:
        return "not verified"
    if any("freshness is stale" in blocker for blocker in blockers):
        return "backup stale"
    if any("freshness is unknown" in blocker for blocker in blockers):
        return "freshness unknown"
    if "Latest backup is not a verified full backup." in blockers:
        return "not full backup"
    if blockers:
        return blockers[0]
    return "blocked"


def _blocker_action(blockers: list[str]) -> str:
    if not blockers:
        return "Rescan or prepare backups."
    if any("freshness is stale" in blocker for blocker in blockers):
        return "Run full backup, then recheck."
    if "No on-disk backup found for production source." in blockers:
        return "Run full backup, then verify."
    if "Latest backup is not artifact-verified (manifest/checksum/size/role)." in blockers:
        return "Verify latest operator backup."
    if "Dev target missing:" in blockers[0]:
        return blockers[0]
    return blockers[0]


def _pair_heading(entry: SyncReadinessEntry) -> str:
    project = f" · {entry.project}" if entry.project else ""
    return f"{entry.prod} → {entry.expected_dev}{project}"


def _pair_route_label(entry: SyncReadinessEntry) -> str:
    prod = _display_sync_database_name(entry.prod)
    dev = _display_sync_database_name(entry.expected_dev)
    return f"{prod} → {dev}"


def _freshness_label(entry: SyncReadinessEntry) -> str:
    if entry.backup_freshness:
        return entry.backup_freshness
    if entry.backup_verified:
        return "verified"
    return "—"


def _sync_status_label(entry: SyncReadinessEntry) -> str:
    if entry.ready_for_sync_planning:
        return "Ready"
    return _compact_readiness_status(ready=False, blockers=entry.blockers)


def sync_menu_context_fields(report: SyncReadinessReport, *, live_allowed: bool) -> dict[str, str]:
    projects = ", ".join(sorted({entry.project for entry in report.entries if entry.project})) or "approved prod→dev pairs"
    fields = {
        "Backup root": report.backup_root,
        "Scope": f"verified prod operator backups into dev only ({projects})",
        "Pairs": f"{report.ready_count} ready · {report.blocked_count} blocked · {len(report.entries)} total",
    }
    fields["Execution"] = "live sync allowed" if live_allowed else "preview only (enable live actions in config)"
    return fields


def sync_menu_next_step(report: SyncReadinessReport, *, live_allowed: bool) -> tuple[str, str]:
    """Return (status_tag, message) for the footer hint above submenu actions."""
    if report.ready_count and not report.blocked_count:
        action = "Sync All Ready Databases" if live_allowed else "Preview All Ready Databases"
        return ("ok", f"All approved pairs are ready — choose [2] {action}.")
    if report.ready_count and report.blocked_count:
        action = "Sync All Ready Databases" if live_allowed else "Preview All Ready Databases"
        return (
            "warn",
            f"{report.ready_count} ready, {report.blocked_count} blocked — sync ready pairs with [2] {action}, "
            "or prepare backups for blocked pairs first.",
        )
    if report.blocked_count:
        return (
            "warn",
            "No pairs ready — run full backup from main menu [1] Backup, then [1] Recheck here "
            "or choose Prepare production backups.",
        )
    return ("info", "No approved sync pairs are configured.")


def sync_menu_table_rows(report: SyncReadinessReport) -> list[list[str]]:
    rows: list[list[str]] = []
    for entry in report.entries:
        rows.append(
            [
                entry.project or "—",
                _pair_route_label(entry),
                entry.backup_age or "—",
                _freshness_label(entry),
                _sync_status_label(entry),
            ]
        )
    return rows


def _print_blocked_actions(report: SyncReadinessReport) -> None:
    blocked = [entry for entry in report.entries if not entry.ready_for_sync_planning]
    if not blocked:
        return
    display_screen.write_blank()
    for entry in blocked:
        display_screen.write_status(
            "fail",
            f"{_pair_heading(entry)} — {_blocker_action(entry.blockers)}",
        )


def _print_sync_readiness_menu(report: SyncReadinessReport, *, live_allowed: bool = True) -> None:
    display_screen.write_fields(sync_menu_context_fields(report, live_allowed=live_allowed))
    display_screen.write_blank()

    rows = sync_menu_table_rows(report)
    table = Table.from_headers(
        ["PROJECT", "PROD → DEV", "BACKUP", "FRESH", "SYNC"],
        rows,
        style=TableStyle(indent=0),
        min_col_widths=[10, 24, 10, 8, 12],
    )
    display_screen.write_structured_table(table)

    _print_blocked_actions(report)

    display_screen.write_blank()
    tag, message = sync_menu_next_step(report, live_allowed=live_allowed)
    display_screen.write_status(tag, message)


def _print_sync_readiness_compact_table(report: SyncReadinessReport) -> None:
    display_screen.write_fields({"Backup root": report.backup_root})
    rows = sync_menu_table_rows(report)
    display_screen.write_blank()
    table = Table.from_headers(
        ["PROJECT", "PROD → DEV", "BACKUP", "FRESH", "SYNC"],
        rows,
        style=TableStyle(indent=0),
        min_col_widths=[10, 24, 10, 8, 12],
    )
    display_screen.write_structured_table(table)


def print_sync_readiness_report(
    report: SyncReadinessReport,
    *,
    compact: bool = False,
    menu: bool = False,
    live_allowed: bool = True,
) -> None:
    if menu:
        _print_sync_readiness_menu(report, live_allowed=live_allowed)
        return

    if compact:
        _print_sync_readiness_compact_table(report)
        return

    output.heading("SYNC READINESS (artifact-verified source backup required)")
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
        if entry.backup_age:
            output.write(f"  backup_age: {entry.backup_age}")
        output.write(f"  backup_verified: {entry.backup_verified}")
        if entry.backup_freshness:
            output.write(f"  backup_freshness: {entry.backup_freshness}")
        for blocker in entry.blockers:
            output.write(f"  - {blocker}")

    output.write()
    output.write(shared_authority_note())
    output.write("Prerequisite: artifact-verified full backup with fresh data before prod→dev sync.")
