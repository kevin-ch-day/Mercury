from mercury.restore.restore_runner import RestoreExecutionResult
from mercury.restore.terminal.runner import print_restore_execution_result


def test_pi_read_window_refusal_prints_exact_temporary_grant_and_revoke(
    capsys,
) -> None:
    print_restore_execution_result(
        RestoreExecutionResult(
            source_database="erebus_threat_intel_prod",
            target_database="_restorecheck_erebus",
            dump_path="/backup/erebus.sql.gz",
            refused=True,
            message=(
                "Restore refused before target modification: a passed restore "
                "privilege preflight is required; "
                "SELECT on `android_permission_intel`.*"
            ),
            cleanup_command="DROP DATABASE IF EXISTS `_restorecheck_erebus`;",
        )
    )

    output = capsys.readouterr().out
    assert "Temporary production PI read window required" in output
    assert "GRANT SELECT ON `android_permission_intel`.*" in output
    assert "account_contract --pi-read-window" in output
    assert "--compact" in output
    assert "REVOKE SELECT ON `android_permission_intel`.*" in output
    assert "Mercury will not grant or revoke" in output


def test_unrelated_restore_refusal_does_not_print_pi_read_window(capsys) -> None:
    print_restore_execution_result(
        RestoreExecutionResult(
            source_database="erebus_threat_intel_prod",
            target_database="_restorecheck_erebus",
            dump_path="/backup/erebus.sql.gz",
            refused=True,
            message="Restore refused before target modification: ALTER ROUTINE",
        )
    )

    output = capsys.readouterr().out
    assert "Temporary production PI read window required" not in output
