"""Display sync execution results."""

from mercury.terminal import screen as display_screen
from mercury.sync.sync_runner import SyncBatchResult


def print_sync_batch_result(batch: SyncBatchResult, *, compact: bool = False) -> None:
    if compact:
        for result in batch.results:
            tag = "ok" if result.executed else "warn" if result.dry_run else "fail"
            label = f"{result.source} -> {result.target}"
            display_screen.write_status(tag, f"{label}: {result.message}")
        return

    for result in batch.results:
        display_screen.write_summary(f"{result.source} -> {result.target}: {result.message}")
