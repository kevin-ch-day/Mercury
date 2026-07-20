"""Terminal rendering for managed offline repository copies."""

from __future__ import annotations

from mercury.repo.offline_clone import OfflineClonePlan, load_offline_sync_receipt
from mercury.terminal.format import format_human_datetime
from mercury.terminal import screen as display_screen


def print_offline_clone_plan(plan: OfflineClonePlan, *, executed: bool = False) -> None:
    display_screen.open_screen("Sync Offline GitHub Repositories")
    synced = sum(1 for entry in plan.entries if entry.executed)
    current = sum(1 for entry in plan.entries if entry.action == "current")
    blocked = sum(1 for entry in plan.entries if entry.action == "blocked")
    pending = sum(1 for entry in plan.entries if entry.action in {"clone", "update"})
    progress = f"{pending} need sync · {current} current · {blocked} blocked"
    display_screen.write_fields(
        {
            "HDD clone root": plan.root,
            "Mode": "EXECUTE" if executed else "PREVIEW",
            "Repositories": len(plan.entries),
            "Status": f"{synced} synced · {current} current · {blocked} blocked" if executed else progress,
        }
    )
    receipt = load_offline_sync_receipt(plan.root)
    if receipt:
        recorded = str(receipt.get("recorded_at_utc", "recorded"))
        display_screen.write_summary(f"Last sync: {format_human_datetime(recorded)}")
    rows = []
    for entry in plan.entries:
        action = "synced" if entry.executed else entry.action
        detail = entry.error or ("source dirty — committed history copied" if entry.source_dirty else "clean source")
        rows.append([entry.display_name, entry.commit[:12], action, detail])
    display_screen.write_blank()
    display_screen.write_compact_table(["REPOSITORY", "COMMIT", "ACTION", "DETAIL"], rows, min_col_widths=[18, 12, 8, 18])
    display_screen.write_blank()
    display_screen.write_summary("Copies are independent Git worktrees on the HDD; source repositories are never modified.")
    display_screen.write_summary("Only committed history is copied. Dirty or untracked source files stay on the source host.")
    if not executed:
        display_screen.write_summary("Preview only. Select [1] to create or update clean offline copies.")
    elif plan.receipt_path:
        display_screen.write_summary(f"Sync evidence: {plan.receipt_path}")


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
        detail = str(entry.get("error") or ("source dirty" if entry.get("source_dirty") else "clean source"))
        rows.append([str(entry.get("display_name", "unknown")), str(entry.get("commit", ""))[:12], state, detail])
    display_screen.write_blank()
    display_screen.write_compact_table(["REPOSITORY", "COMMIT", "RESULT", "DETAIL"], rows, min_col_widths=[18, 12, 8, 16])
