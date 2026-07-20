"""Terminal rendering for managed offline repository copies."""

from __future__ import annotations

from mercury.repo.offline_clone import OfflineClonePlan, load_offline_sync_receipt
from mercury.terminal import screen as display_screen


def print_offline_clone_plan(plan: OfflineClonePlan, *, executed: bool = False) -> None:
    display_screen.open_screen("Offline Repository Copies")
    synced = sum(1 for entry in plan.entries if entry.executed)
    current = sum(1 for entry in plan.entries if entry.action == "current")
    blocked = sum(1 for entry in plan.entries if entry.action == "blocked")
    display_screen.write_fields(
        {
            "HDD clone root": plan.root,
            "Mode": "EXECUTE" if executed else "PREVIEW",
            "Scope": f"{len(plan.entries)} migration repository/repositories",
            "Result": f"{synced} synced · {current} current · {blocked} blocked" if executed else "No files written",
        }
    )
    receipt = load_offline_sync_receipt(plan.root)
    if receipt:
        display_screen.write_summary(f"Last sync evidence: {receipt.get('recorded_at_utc', 'recorded')}")
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
        display_screen.write_summary("Preview only. Select [2] to create or update clean offline copies.")
    elif plan.receipt_path:
        display_screen.write_summary(f"Sync evidence: {plan.receipt_path}")
