"""Tests for restore execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mercury.backup.backup_runner import BackupExecutionError
from mercury.cli import app
from mercury.core.execution_policy import ExecutionPolicy
from mercury.restore.restore_runner import assert_safe_restore_target, execute_restore_into_database
from mercury.restore.interactive_menu import run_restore_menu


def test_assert_safe_restore_target_allows_dev() -> None:
    assert_safe_restore_target("erebus_threat_intel_dev")


def test_assert_safe_restore_target_blocks_prod() -> None:
    with pytest.raises(BackupExecutionError):
        assert_safe_restore_target("erebus_threat_intel_prod")


def test_execute_restore_dry_run(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fake")
    policy = ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=tmp_path)
    result = execute_restore_into_database(
        target_database="erebus_threat_intel_dev",
        dump_path=dump,
        source_database="erebus_threat_intel_prod",
        execute=False,
        policy=policy,
    )
    assert result.dry_run is True
    assert "Would restore" in result.message
    assert result.commands


def test_execute_restore_refused_when_dry_run_policy(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fake")
    policy = ExecutionPolicy(dry_run=True, live_actions_enabled=True, backup_root=tmp_path)
    result = execute_restore_into_database(
        target_database="erebus_threat_intel_dev",
        dump_path=dump,
        source_database="erebus_threat_intel_prod",
        execute=True,
        policy=policy,
    )
    assert result.refused is True
    assert result.executed is False


def test_execute_restore_live_uses_runner(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fake")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    calls: list[str] = []

    def fake_runner(argv, env, dump_path, config, target) -> None:
        calls.append(target)

    from mercury.database.mariadb.config import MariaDbConnectionConfig

    config = MariaDbConnectionConfig(
        host="localhost",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )

    def fake_sql(cfg, sql: str) -> None:
        calls.append(sql)

    import mercury.restore.restore_runner as restore_execute

    restore_execute._execute_client_sql = fake_sql  # type: ignore[method-assign]
    try:
        result = execute_restore_into_database(
            target_database="erebus_threat_intel_dev",
            dump_path=dump,
            source_database="erebus_threat_intel_prod",
            execute=True,
            policy=policy,
            config=config,
            import_runner=fake_runner,
        )
    finally:
        from mercury.restore.restore_runner import _execute_client_sql as original

        restore_execute._execute_client_sql = original  # type: ignore[method-assign]

    assert result.executed is True
    assert calls


def test_restore_check_auto_drops_temp_database_on_success(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fake")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    calls: list[str] = []

    def fake_runner(argv, env, dump_path, config, target) -> None:
        calls.append(f"import:{target}")

    from mercury.database.mariadb.config import MariaDbConnectionConfig

    config = MariaDbConnectionConfig(
        host="localhost",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )

    def fake_sql(cfg, sql: str) -> None:
        calls.append(sql)

    import mercury.restore.restore_runner as restore_execute

    original = restore_execute._execute_client_sql
    restore_execute._execute_client_sql = fake_sql  # type: ignore[method-assign]
    try:
        result = execute_restore_into_database(
            target_database="_restorecheck_erebus_threat_intel_prod_20260608",
            dump_path=dump,
            source_database="erebus_threat_intel_prod",
            execute=True,
            policy=policy,
            config=config,
            import_runner=fake_runner,
            cleanup_after_success=True,
        )
    finally:
        restore_execute._execute_client_sql = original  # type: ignore[method-assign]

    assert result.executed is True
    assert result.cleanup_dropped is True
    assert result.cleanup_command == "DROP DATABASE IF EXISTS `_restorecheck_erebus_threat_intel_prod_20260608`"
    assert any("DROP DATABASE IF EXISTS `_restorecheck_erebus_threat_intel_prod_20260608`" == call for call in calls)


def test_restore_check_preserves_temp_database_on_failure(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fake")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )

    def failing_runner(argv, env, dump_path, config, target) -> None:
        raise BackupExecutionError("import failed")

    from mercury.database.mariadb.config import MariaDbConnectionConfig

    config = MariaDbConnectionConfig(
        host="localhost",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )

    result = execute_restore_into_database(
        target_database="_restorecheck_erebus_threat_intel_prod_20260608",
        dump_path=dump,
        source_database="erebus_threat_intel_prod",
        execute=True,
        policy=policy,
        config=config,
        import_runner=failing_runner,
        cleanup_after_success=True,
    )

    assert result.executed is False
    assert result.refused is True
    assert result.cleanup_dropped is False
    assert result.cleanup_command == "DROP DATABASE IF EXISTS `_restorecheck_erebus_threat_intel_prod_20260608`"
    assert "preserved for debugging" in result.message


def test_execute_restore_marks_verification_failure_and_preserves_restorecheck_db(tmp_path: Path) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fake")
    (tmp_path / "manifest.json").write_text('{"database":"erebus_threat_intel_prod"}', encoding="utf-8")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    calls: list[str] = []

    def fake_runner(argv, env, dump_path, config, target) -> None:
        calls.append(f"import:{target}")

    from mercury.database.mariadb.config import MariaDbConnectionConfig

    config = MariaDbConnectionConfig(
        host="localhost",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )

    def fake_sql(cfg, sql: str) -> None:
        calls.append(sql)

    def fake_verify(*args, **kwargs):
        from mercury.deploy.models import DeploymentVerification

        return DeploymentVerification(
            database="_restorecheck_erebus_threat_intel_prod_20260608",
            exists_on_server=True,
            table_count=0,
            verified=False,
            detail="basic verification only",
            issues=["table count is zero"],
        )

    import mercury.restore.restore_runner as restore_execute

    original_sql = restore_execute._execute_client_sql
    original_verify = restore_execute._verify_restore_target
    restore_execute._execute_client_sql = fake_sql  # type: ignore[method-assign]
    restore_execute._verify_restore_target = fake_verify  # type: ignore[assignment]
    try:
        result = execute_restore_into_database(
            target_database="_restorecheck_erebus_threat_intel_prod_20260608",
            dump_path=dump,
            source_database="erebus_threat_intel_prod",
            execute=True,
            policy=policy,
            config=config,
            import_runner=fake_runner,
            cleanup_after_success=True,
        )
    finally:
        restore_execute._execute_client_sql = original_sql  # type: ignore[method-assign]
        restore_execute._verify_restore_target = original_verify  # type: ignore[assignment]

    assert result.executed is True
    assert result.verification_passed is False
    assert result.cleanup_dropped is False
    assert "verification failed" in result.message.lower()
    assert calls.count("DROP DATABASE IF EXISTS `_restorecheck_erebus_threat_intel_prod_20260608`") == 1


def test_restore_check_run_cli_exits_nonzero_on_post_import_verification_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mercury.restore.check_plan import RestoreCheckPlan
    from mercury.restore.restore_runner import RestoreExecutionResult

    runner = CliRunner()
    backup_dir = tmp_path / "backups" / "2026-06-11" / "erebus_threat_intel_prod"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "erebus.sql.gz").write_bytes(b"fake")

    monkeypatch.setattr(
        "mercury.restore.check_plan.build_restore_check_plan",
        lambda db, **_kwargs: RestoreCheckPlan(
            source_prod=db,
            restore_target="_restorecheck_erebus_threat_intel_prod_20260611",
            backup_directory=str(backup_dir),
            dump_file="erebus.sql.gz",
            backup_verified=True,
            backup_id="erebus_threat_intel_prod-full-20260611_120000",
            allowed=True,
            planned_commands=[],
            safety_notes=[],
        ),
    )
    monkeypatch.setattr(
        "mercury.restore.restore_runner.execute_restore_into_database",
        lambda **kwargs: RestoreExecutionResult(
            source_database="erebus_threat_intel_prod",
            target_database="_restorecheck_erebus_threat_intel_prod_20260611",
            dump_path=str(backup_dir / "erebus.sql.gz"),
            dry_run=False,
            executed=True,
            message="Imported backup, but target verification failed.",
            verification_passed=False,
        ),
    )

    result = runner.invoke(
        app,
        [
            "restore-check",
            "run",
            "--db",
            "erebus_threat_intel_prod",
            "--backup-id",
            "erebus_threat_intel_prod-full-20260611_120000",
            "--execute",
        ],
    )

    assert result.exit_code == 1
    assert "verification failed" in result.stdout.lower()

# merged from test_restore_menu.py
def test_run_restore_menu_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.core.execution_policy import ExecutionPolicy

    monkeypatch.setattr("mercury.restore.interactive_menu._load_plans", lambda: [])
    monkeypatch.setattr(
        "mercury.restore.interactive_menu.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=Path("/tmp/mercury-restore-menu"),
            allow_unsafe_backup_root=True,
        ),
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_menu._restorecheck_names_on_server",
        lambda: [],
    )
    run_restore_menu(interactive=False)
    out = capsys.readouterr().out
    assert "Restore-check Operations" in out
    assert "No backup sources found" in out
    assert "\n      [1] Refresh" in out
    assert "Run restore-checks (none ready)" in out
    assert "[0] Back" in out
