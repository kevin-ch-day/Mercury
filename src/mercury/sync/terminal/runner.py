"""Display sync execution results."""

from mercury.terminal import screen as display_screen
from mercury.sync.sync_runner import SyncBatchResult


def print_sync_batch_result(batch: SyncBatchResult, *, compact: bool = False) -> None:
    if compact:
        for result in batch.results:
            tag = (
                "fail"
                if result.verification_passed is False
                else "ok" if result.executed else "warn" if result.dry_run else "fail"
            )
            label = f"{result.source} -> {result.target}"
            message = result.message
            if result.dry_run and message and not message.lower().startswith("preview only"):
                message = f"Preview only — {message[0].lower()}{message[1:]}" if len(message) > 1 else f"Preview only — {message.lower()}"
            display_screen.write_status(tag, f"{label}: {message}")
        return

    for result in batch.results:
        display_screen.write_summary(f"{result.source} -> {result.target}: {result.message}")
