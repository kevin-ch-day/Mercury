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
    policy = ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=tmp_path)
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
