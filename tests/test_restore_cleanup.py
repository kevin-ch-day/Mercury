"""Tests for restore-check cleanup."""

from __future__ import annotations

import pytest

from mercury.backup.backup_runner import BackupExecutionError
from mercury.restore.check_cleanup import (
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
