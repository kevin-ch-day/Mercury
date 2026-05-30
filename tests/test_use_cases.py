"""Tests for expanded Mercury use cases."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mercury.backup.batch import resolve_batch_sources, run_backup_batch
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.restore.check import build_restore_check_plan, planned_restore_check_name
from mercury.sync.readiness import build_sync_readiness_report


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
    report = build_sync_readiness_report(live=False)
    assert report.entries
    assert report.blocked_count >= 1


def test_restore_check_plan_requires_verified_backup(tmp_path: Path) -> None:
    policy = ExecutionPolicy(dry_run=True, live_actions_enabled=False, backup_root=tmp_path)
    plan = build_restore_check_plan("erebus_threat_intel_prod")
    assert plan.source_prod == "erebus_threat_intel_prod"
    assert plan.restore_target.startswith("_restorecheck_")
    assert plan.allowed is False
    assert plan.blockers


def test_planned_restore_check_name_format() -> None:
    name = planned_restore_check_name("erebus_threat_intel_prod", date="20260530")
    assert name == "_restorecheck_erebus_threat_intel_prod_20260530"


def test_cli_backup_batch_dry_run() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "backup", "batch", "--demo"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BACKUP BATCH" in result.stdout


def test_cli_sync_readiness() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "sync", "readiness"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SYNC READINESS" in result.stdout


def test_cli_restore_check_plan() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mercury.cli",
            "restore-check",
            "plan",
            "--db",
            "erebus_threat_intel_prod",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "RESTORE-CHECK" in result.stdout


def test_cli_sync_plan_live_or_demo() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "sync", "plan", "--demo"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "sync plan" in result.stdout.lower()
