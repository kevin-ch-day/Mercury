"""Execution-bound confirmation tests for destructive prod→dev sync."""

from pathlib import Path

from mercury.core.execution_policy import ExecutionPolicy
from mercury.sync.readiness import SyncReadinessEntry
from mercury.sync.sync_runner import run_sync_batch


def test_live_sync_refuses_without_exact_confirmation_before_preflight() -> None:
    entry = SyncReadinessEntry(
        prod="erebus_threat_intel_prod",
        expected_dev="erebus_threat_intel_dev",
        dev_listed=True,
        ready_for_sync_planning=True,
    )
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=Path("/tmp/backups"),
        config_path=None,
        allow_unsafe_backup_root=True,
    )

    result = run_sync_batch([entry], execute=True, policy=policy)

    assert result.executed_count == 0
    assert result.refused_count == 1
    assert "SYNC DEV" in result.results[0].message
