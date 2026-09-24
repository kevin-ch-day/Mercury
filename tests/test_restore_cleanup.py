"""Tests for restore-check cleanup."""

from __future__ import annotations

import pytest

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.restore.check_cleanup import (
    PHASE3B_RETAINED_RESTORECHECK,
    assert_restorecheck_database,
    cleanup_restorecheck_databases,
    drop_restorecheck_database,
    is_restorecheck_database,
    list_restorecheck_databases,
)


def test_is_restorecheck_database() -> None:
    assert is_restorecheck_database("_restorecheck_erebus_threat_intel_prod_20260530")
    assert not is_restorecheck_database("erebus_threat_intel_prod")


def test_list_restorecheck_databases_filters() -> None:
    names = [
        "erebus_threat_intel_prod",
        "_restorecheck_erebus_threat_intel_prod_20260530",
        "mysql",
    ]
    assert list_restorecheck_databases(names) == [
        "_restorecheck_erebus_threat_intel_prod_20260530",
    ]


def test_assert_restorecheck_database_blocks_prod() -> None:
    with pytest.raises(BackupExecutionError):
        assert_restorecheck_database("erebus_threat_intel_prod")


def test_drop_restorecheck_dry_run() -> None:
    result = drop_restorecheck_database(
        "_restorecheck_erebus_threat_intel_prod_20260530",
        execute=False,
    )
    assert result.dry_run is True
    assert "Would drop" in result.message


def test_cleanup_batch_dry_run() -> None:
    batch = cleanup_restorecheck_databases(
        [
            "erebus_threat_intel_prod",
            "_restorecheck_erebus_threat_intel_prod_20260530",
        ],
        execute=False,
    )
    assert batch.mode == "dry-run"
    assert len(batch.databases) == 1
    assert batch.results[0].dry_run is True


def test_live_cleanup_defaults_to_dedicated_restore_account(monkeypatch) -> None:
    config = MariaDbConnectionConfig(
        host="localhost",
        user="mercury_dev_restore",
        unix_socket="/var/lib/mysql/mysql.sock",
        use_client=True,
    )
    captured = {}
    monkeypatch.setattr(
        "mercury.restore.check_cleanup.try_load_mariadb_restore_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "mercury.restore.check_cleanup.run_client_sql",
        lambda actual, sql: captured.update(config=actual, sql=sql),
    )

    result = drop_restorecheck_database(
        "_restorecheck_erebus_threat_intel_prod_20260530",
        execute=True,
    )

    assert result.dropped is True
    assert captured["config"] is config
    assert captured["sql"].startswith("DROP DATABASE IF EXISTS")


def test_generic_cleanup_refuses_identifier_injection() -> None:
    with pytest.raises(BackupExecutionError, match="Unsafe SQL restore-check database"):
        drop_restorecheck_database(
            "_restorecheck_x`; DROP DATABASE `erebus_threat_intel_prod",
            execute=False,
        )


def test_generic_cleanup_refuses_retained_phase3b_schemas() -> None:
    name = next(iter(PHASE3B_RETAINED_RESTORECHECK))
    result = drop_restorecheck_database(name, execute=False)
    assert result.refused is True
    assert "retire-phase3b-restorecheck" in result.message


def test_phase3b_drop_plan_omits_if_exists() -> None:
    name = "_restorecheck_pi_fixture_aaaa"
    result = drop_restorecheck_database(
        name, execute=False, allow_phase3b=True, if_exists=False,
    )
    assert result.refused is False
    assert "DROP DATABASE IF EXISTS" not in result.message
    assert f"DROP DATABASE `{name}`" in result.message
