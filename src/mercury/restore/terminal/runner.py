"""Display restore execution results."""

from mercury.terminal import screen as display_screen
from mercury.restore.restore_runner import RestoreExecutionResult


def print_restore_execution_result(result: RestoreExecutionResult, *, compact: bool = False) -> None:
    if compact:
        tag = "ok" if result.executed else "warn" if result.dry_run else "fail"
        display_screen.write_status(
            tag,
            f"{result.source_database} -> {result.target_database}: {result.message}",
        )
        return

    display_screen.write_summary(
        f"{result.source_database} -> {result.target_database}: {result.message}"
    )
