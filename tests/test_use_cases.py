"""Tests for expanded Mercury use cases."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mercury.backup.batch_runner import resolve_batch_sources, run_backup_batch
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.restore.check_plan import build_restore_check_plan, planned_restore_check_name
from mercury.sync.readiness import build_sync_readiness_report
from mercury.core.paths import REPO_ROOT
from tests.conftest import run_cli


def test_resolve_batch_sources_includes_prod() -> None:
    sources = resolve_batch_sources(live=False)
    assert "erebus_threat_intel_prod" in sources
    assert "erebus_threat_intel_dev" not in sources


def test_run_backup_batch_dry_run(tmp_path: Path) -> None:
    policy = ExecutionPolicy(dry_run=True, live_actions_enabled=False, backup_root=tmp_path)
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=False,
        live=False,
        policy=policy,
        sources=["android_permission_intel"],
    )
    assert batch.dry_run_count == 1
    assert batch.executed_count == 0


def test_sync_readiness_report_demo() -> None:
    import mercury.sync.readiness as readiness

    original = readiness.load_execution_policy
    readiness.load_execution_policy = lambda: ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=Path("/tmp/mercury-empty-sync-readiness"),
        allow_unsafe_backup_root=True,
    )
    try:
        report = build_sync_readiness_report(live=False)
    finally:
        readiness.load_execution_policy = original
    assert report.entries
    assert report.blocked_count >= 1


def test_sync_readiness_ignores_repo_local_backups_for_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.sync.readiness.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=REPO_ROOT / "backups",
            config_path=Path("/tmp/local.toml"),
        ),
    )
    report = build_sync_readiness_report(live=False)
    assert report.ready_count == 0
    assert all(
        any("repo-local fallback" in blocker for blocker in entry.blockers)
        for entry in report.entries
    )


def test_restore_check_plan_requires_verified_backup(tmp_path: Path) -> None:
    import mercury.restore.check_plan as restore_check_plan

    original = restore_check_plan.load_execution_policy
    restore_check_plan.load_execution_policy = lambda: ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path,
        allow_unsafe_backup_root=True,
    )
    try:
        plan = build_restore_check_plan("erebus_threat_intel_prod")
    finally:
        restore_check_plan.load_execution_policy = original
    assert plan.source_prod == "erebus_threat_intel_prod"
    assert plan.restore_target.startswith("_restorecheck_")
    assert plan.allowed is False
    assert plan.blockers


def test_planned_restore_check_name_format() -> None:
    name = planned_restore_check_name("erebus_threat_intel_prod", date="20260530")
    assert name == "_restorecheck_erebus_threat_intel_prod_20260530"


def test_cli_backup_batch_dry_run() -> None:
    result = run_cli("backup", "batch", "--demo", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BACKUP BATCH" in result.stdout


def test_cli_sync_readiness() -> None:
    result = run_cli("sync", "readiness")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Backup root:" in result.stdout
    assert "PROJECT" in result.stdout
    assert "PROD → DEV" in result.stdout
    assert "SYNC" in result.stdout
    assert "erebus_threat_intel" in result.stdout


def test_cli_restore_check_plan() -> None:
    env = os.environ.copy()
    env["MERCURY_BACKUP_ROOT"] = "/tmp/mercury-empty-restore-check"
    env["MERCURY_ALLOW_UNSAFE_BACKUP_ROOT"] = "1"
    result = run_cli(
        "restore-check",
        "plan",
        "--db",
        "erebus_threat_intel_prod",
        env=env,
    )
    assert result.returncode != 0
    assert "RESTORE-CHECK" in result.stdout


def test_cli_sync_plan_live_or_demo() -> None:
    result = run_cli("sync", "plan", "--demo")
    assert result.returncode == 0
    assert "sync plan" in result.stdout.lower()
