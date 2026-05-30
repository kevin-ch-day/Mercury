"""Display restore-check cleanup results."""

from mercury.terminal import screen as display_screen
from mercury.restore.check_cleanup import RestoreCheckCleanupBatch, RestoreCheckCleanupResult


def print_restorecheck_cleanup_result(result: RestoreCheckCleanupResult, *, compact: bool = False) -> None:
    if compact:
        tag = "ok" if result.dropped else "warn" if result.dry_run else "fail"
        display_screen.write_status(tag, f"{result.database}: {result.message}")
        return
    display_screen.write_summary(f"{result.database}: {result.message}")


def print_restorecheck_cleanup_batch(batch: RestoreCheckCleanupBatch, *, compact: bool = False) -> None:
    if not batch.databases:
        display_screen.write_status("ok", "No _restorecheck_* databases found on server.")
        return

    if compact:
        display_screen.write_fields(
            {
                "mode": batch.mode,
                "targets": len(batch.databases),
            }
        )
    else:
        display_screen.write_summary(
            f"Restore-check cleanup ({batch.mode}): {len(batch.databases)} target(s)."
        )

    for result in batch.results:
        print_restorecheck_cleanup_result(result, compact=compact)

    if compact and batch.results:
        dropped = batch.dropped_count
        if batch.mode == "dry-run":
            display_screen.write_summary(f"Would drop {len(batch.databases)} restore-check database(s).")
        else:
            display_screen.write_summary(f"Dropped {dropped} of {len(batch.databases)} restore-check database(s).")
