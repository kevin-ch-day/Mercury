"""Terminal rendering for managed offline repository copies."""

from __future__ import annotations

from pathlib import Path

from mercury import output
from mercury.repo.offline_clone import OfflineClonePlan, load_offline_sync_receipt, snapshot_is_current
from mercury.terminal.format import format_human_datetime
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.terminal.theme import hint_text


def _write_dense(lines: list[str]) -> None:
    for line in lines:
        output.write(hint_text(line))


def _source_detail(dirty: bool, snapshot: object, mirror: str | Path | None, captured: object) -> str:
    if not dirty:
        return "clean source"
    if snapshot_is_current(snapshot, mirror, captured):
        return "dirty snapshot recorded"
    return "source dirty (committed only)"


def print_offline_clone_plan(
    plan: OfflineClonePlan,
    *,
    executed: bool = False,
    with_title: bool = True,
    title: str = "Repository backups",
) -> None:
    if with_title:
        display_screen.open_screen(title)
    synced = sum(1 for entry in plan.entries if entry.executed)
    reused = sum(1 for entry in plan.entries if entry.executed and entry.mirror_reused)
    current = sum(1 for entry in plan.entries if entry.action == "current" and not entry.executed)
    blocked = sum(1 for entry in plan.entries if entry.action == "blocked")
    pending = sum(1 for entry in plan.entries if entry.action in {"clone", "update"})
    progress = f"{pending} need sync · {current} current · {blocked} blocked"
    status = (
        f"{synced} synced · {current} current · {blocked} blocked" if executed else progress
    )
    fields: dict[str, object] = {
        "Clone root": plan.root,
        "Mode": "EXECUTE" if executed else "PREVIEW",
        "Repositories": len(plan.entries),
        "Status": status,
    }
    if reused:
        fields["Mirrors reused"] = reused
    receipt = load_offline_sync_receipt(plan.root)
    if receipt:
        recorded = str(receipt.get("recorded_at_utc", "recorded"))
        fields["Last sync"] = format_human_datetime(recorded)
    display_screen.write_fields(fields)

    rows = []
    for entry in plan.entries:
        action = ("reused" if entry.mirror_reused else "synced") if entry.executed else entry.action
        detail = entry.error or _source_detail(
            entry.source_dirty, entry.snapshot, entry.destination_path if entry.executed else None,
            entry.snapshot_captured,
        )
        rows.append([entry.display_name, entry.commit[:12], action, detail])
    display_screen.write_structured_table(
        Table.from_headers(
            ["REPOSITORY", "COMMIT", "ACTION", "DETAIL"],
            rows,
            style=TableStyle(indent=0, gap=2),
            min_col_widths=[14, 12, 7, 16],
            max_col_widths=[max([22] + [len(e.display_name) for e in plan.entries]), 12, 8, 40],
        )
    )
    for entry in plan.entries:
        if entry.source_dirty:
            stamp = format_human_datetime(entry.snapshot_at) if entry.snapshot_at else "none"
            output.write(f"{entry.display_name}: {entry.uncommitted_count} uncommitted files, last snapshot {stamp}")
            if entry.snapshot and entry.snapshot.get("excluded_count"):
                output.write(f"  Snapshot excludes {entry.snapshot['excluded_count']} sensitive, ignored, or unsafe files; see snapshot.json.")
    notes = [
        "Independent copies on backup storage; source repos are never modified.",
        "Mirrors cover all branches, tags and remote-tracking refs; uncommitted snapshots are optional.",
    ]
    if not executed:
        notes.append("Preview only — select [1] to create or update offline copies.")
    elif plan.receipt_path:
        notes.append(f"Sync evidence: {plan.receipt_path}")
    _write_dense(notes)


def print_offline_sync_receipt(plan: OfflineClonePlan) -> None:
    """Show the latest saved offline-sync evidence without touching repositories."""
    display_screen.open_screen("Repository backup receipt")
    receipt = load_offline_sync_receipt(plan.root)
    if not receipt:
        display_screen.write_status("info", "No sync receipt has been recorded yet.")
        display_screen.write_summary("Run [1] Sync Offline GitHub Repositories to create one.")
        return
    repositories = receipt.get("repositories")
    entries = repositories if isinstance(repositories, list) else []
    display_screen.write_fields(
        {
            "Recorded": format_human_datetime(str(receipt.get("recorded_at_utc", "unknown"))),
            "Clone root": receipt.get("clone_root", plan.root),
            "Repositories": len(entries),
        }
    )
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        state = str(entry.get("action", "unknown"))
        if entry.get("synced"):
            state = "reused" if entry.get("mirror_reused") is True else "synced"
        mirror = entry.get("mirror_path")
        detail = str(entry.get("error") or _source_detail(
            bool(entry.get("source_dirty")), entry.get("snapshot"),
            mirror if isinstance(mirror, str) and entry.get("synced") else None,
            entry.get("snapshot_captured"),
        ))
        rows.append(
            [str(entry.get("display_name", "unknown")), str(entry.get("commit", ""))[:12], state, detail]
        )
    display_screen.write_structured_table(
        Table.from_headers(
            ["REPOSITORY", "COMMIT", "RESULT", "DETAIL"],
            rows,
            style=TableStyle(indent=0, gap=2),
            min_col_widths=[14, 12, 7, 16],
            max_col_widths=[max([22] + [len(e.display_name) for e in plan.entries]), 12, 8, 40],
        )
    )
