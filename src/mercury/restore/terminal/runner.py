"""Display restore execution results."""

from mercury.restore.restore_runner import RestoreExecutionResult
from mercury.terminal import screen as display_screen

_PI_READ_CAPABILITY = "SELECT on `android_permission_intel`.*"


def _print_refusal_recovery(result: RestoreExecutionResult) -> None:
    """Explain the exact temporary read window without performing DCL."""
    if _PI_READ_CAPABILITY not in (result.message or ""):
        return
    display_screen.write_summary("Temporary production PI read window required:")
    display_screen.write_summary(
        "  1. sudo mariadb -e \"GRANT SELECT ON `android_permission_intel`.* "
        "TO 'mercury_dev_restore'@'localhost';\""
    )
    display_screen.write_summary(
        "  2. .venv/bin/python -m mercury.restore.account_contract "
        "--pi-read-window --compact"
    )
    display_screen.write_summary("  3. Re-run this exact restore-check command.")
    display_screen.write_summary(
        "  4. After success, failure, or interruption: sudo mariadb -e \"REVOKE "
        "SELECT ON `android_permission_intel`.* FROM "
        "'mercury_dev_restore'@'localhost';\""
    )
    display_screen.write_summary(
        "  5. .venv/bin/python -m mercury.restore.account_contract --compact"
    )
    display_screen.write_summary(
        "Mercury will not grant or revoke this DBA-owned temporary privilege."
    )


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
    if result.refused:
        _print_refusal_recovery(result)
    if result.cleanup_command and not result.cleanup_dropped:
        display_screen.write_summary(f"Cleanup: {result.cleanup_command}")
