"""Terminal rendering for managed offline repository copies."""

from __future__ import annotations

from mercury import output
from mercury.repo.offline_clone import OfflineClonePlan, load_offline_sync_receipt
from mercury.terminal.format import format_human_datetime
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.terminal.theme import hint_text


def _write_dense(lines: list[str]) -> None:
    for line in lines:
        output.write(hint_text(line))


def print_offline_clone_plan(plan: OfflineClonePlan, *, executed: bool = False) -> None:
    display_screen.open_screen("Sync Offline GitHub Repositories")
    synced = sum(1 for entry in plan.entries if entry.executed)
    current = sum(1 for entry in plan.entries if entry.action == "current")
    blocked = sum(1 for entry in plan.entries if entry.action == "blocked")
    pending = sum(1 for entry in plan.entries if entry.action in {"clone", "update"})
    progress = f"{pending} need sync · {current} current · {blocked} blocked"
    status = (
        f"{synced} synced · {current} current · {blocked} blocked" if executed else progress
    )
    fields: dict[str, object] = {
        "HDD clone root": plan.root,
        "Mode": "EXECUTE" if executed else "PREVIEW",
        "Repositories": len(plan.entries),
        "Status": status,
    }
    receipt = load_offline_sync_receipt(plan.root)
    if receipt:
        recorded = str(receipt.get("recorded_at_utc", "recorded"))
        fields["Last sync"] = format_human_datetime(recorded)
    display_screen.write_fields(fields)

    rows = []
    for entry in plan.entries:
        action = "synced" if entry.executed else entry.action
        if entry.error:
            detail = entry.error
        elif entry.source_dirty:
            detail = "source dirty (committed only)"
        else:
            detail = "clean source"
        rows.append([entry.display_name, entry.commit[:12], action, detail])
    display_screen.write_structured_table(
        Table.from_headers(
            ["REPOSITORY", "COMMIT", "ACTION", "DETAIL"],
            rows,
            style=TableStyle(indent=0, gap=2),
            min_col_widths=[14, 12, 7, 16],
            max_col_widths=[22, 12, 8, 40],
        )
    )
    notes = [
        "Independent HDD worktrees; source repos are never modified.",
        "Only committed history is copied; dirty/untracked files stay on the source host.",
    ]
    if not executed:
        notes.append("Preview only — select [1] to create or update offline copies.")
    elif plan.receipt_path:
        notes.append(f"Sync evidence: {plan.receipt_path}")
    _write_dense(notes)


def print_offline_sync_receipt(plan: OfflineClonePlan) -> None:
    """Show the latest saved offline-sync evidence without touching repositories."""
    display_screen.open_screen("Offline GitHub Sync Receipt")
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
        state = "synced" if entry.get("synced") else str(entry.get("action", "unknown"))
        detail = str(
            entry.get("error")
            or ("source dirty (committed only)" if entry.get("source_dirty") else "clean source")
        )
        rows.append(
            [str(entry.get("display_name", "unknown")), str(entry.get("commit", ""))[:12], state, detail]
        )
    display_screen.write_structured_table(
        Table.from_headers(
            ["REPOSITORY", "COMMIT", "RESULT", "DETAIL"],
            rows,
            style=TableStyle(indent=0, gap=2),
            min_col_widths=[14, 12, 7, 16],
            max_col_widths=[22, 12, 8, 40],
        )
    )
