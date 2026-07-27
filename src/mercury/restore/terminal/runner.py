"""Display restore execution results."""

from mercury.terminal import screen as display_screen
from mercury.restore.restore_runner import RestoreExecutionResult


def print_restore_execution_result(result: RestoreExecutionResult, *, compact: bool = False) -> None:
    if compact:
        if result.verification_passed is False:
            detail = "verification failed"
            if result.verification_issues:
                detail = result.verification_issues[0]
            elif result.message:
                detail = result.message
            if len(detail) > 72:
                detail = detail[:69] + "..."
            display_screen.write_status("fail", f"{result.source_database}: fail · {detail}")
            if result.cleanup_command and not result.cleanup_dropped:
                display_screen.write_summary(f"Cleanup: {result.cleanup_command}")
            return

        if result.executed:
            bits = ["ok"]
            if result.verification_passed:
                bits.append("verified")
            if result.cleanup_dropped:
                bits.append("cleaned")
            elif result.cleanup_command:
                bits.append("cleanup needed")
            display_screen.write_status("ok", f"{result.source_database}: {' · '.join(bits)}")
            if result.cleanup_command and not result.cleanup_dropped:
                display_screen.write_summary(f"Cleanup: {result.cleanup_command}")
            return

        tag = "warn" if result.dry_run else "fail"
        display_screen.write_status(
            tag,
            f"{result.source_database}: {result.message}",
        )
        return

    display_screen.write_summary(
        f"{result.source_database} -> {result.target_database}: {result.message}"
    )
    if result.cleanup_command and not result.cleanup_dropped:
        display_screen.write_summary(f"Cleanup: {result.cleanup_command}")
