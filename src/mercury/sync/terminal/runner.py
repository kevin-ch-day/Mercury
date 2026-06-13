"""Display sync execution results."""

from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.sync.sync_runner import SyncBatchResult


def _result_status(result) -> str:
    if result.verification_passed is False or result.refused:
        return "Failed"
    if result.executed:
        return "Synced"
    if result.dry_run:
        return "Preview"
    return "Blocked"


def _result_detail(result) -> str:
    message = result.message or ""
    if result.dry_run and not result.refused and message and not message.lower().startswith("preview only"):
        if len(message) > 1:
            return f"Preview only — {message[0].lower()}{message[1:]}"
        return f"Preview only — {message.lower()}"
    if result.executed and result.verification_passed is not False and not result.refused:
        return "Dev target refreshed from verified USB backup."
    return message or "No action taken."


def print_sync_batch_result(batch: SyncBatchResult, *, compact: bool = False) -> None:
    if compact:
        rows = [
            [
                result.source,
                result.target,
                _result_status(result),
                _result_detail(result),
            ]
            for result in batch.results
        ]
        if rows:
            display_screen.write_blank()
            table = Table.from_headers(
                ["PRODUCTION", "DEVELOPMENT", "RESULT", "DETAIL"],
                rows,
                style=TableStyle(indent=0),
                min_col_widths=[24, 24, 10, 24],
            )
            display_screen.write_structured_table(table)

        executed = sum(1 for result in batch.results if result.executed and result.verification_passed is not False)
        failed = sum(1 for result in batch.results if result.refused or result.verification_passed is False)
        preview = sum(1 for result in batch.results if result.dry_run and not result.refused)
        if executed:
            display_screen.write_status("ok", f"Synced {executed} development target(s) from verified USB backups.")
        elif preview:
            display_screen.write_summary(f"Preview complete — {preview} pair(s); no dev databases were changed.")
        elif failed:
            display_screen.write_status("fail", f"Sync did not complete for {failed} pair(s). Review DETAIL above.")
        return

    for result in batch.results:
        display_screen.write_summary(f"{result.source} -> {result.target}: {result.message}")
