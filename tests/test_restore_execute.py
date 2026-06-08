"""Tests for restore execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.backup.backup_runner import BackupExecutionError
from mercury.core.execution_policy import ExecutionPolicy
from mercury.restore.restore_runner import assert_safe_restore_target, execute_restore_into_database


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
