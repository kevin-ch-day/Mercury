from __future__ import annotations

import pytest

from mercury.backup.backup_runner import BackupExecutionError, assert_safe_backup_source
from mercury.backup.batch_runner import resolve_development_backup_sources


def test_development_backup_remains_refused_without_explicit_override() -> None:
    with pytest.raises(BackupExecutionError):
        assert_safe_backup_source("erebus_threat_intel_dev")


def test_only_configured_development_target_is_allowed_explicitly() -> None:
    assert assert_safe_backup_source("erebus_threat_intel_dev", allow_development_backup=True).role.value == "development"
    assert assert_safe_backup_source("android_permission_intel_dev", allow_development_backup=True).role.value == "development"
    with pytest.raises(BackupExecutionError):
        assert_safe_backup_source("proofpoint_cti_db_dev", allow_development_backup=True)


def test_development_sources_only_include_present_configured_targets(monkeypatch) -> None:
    class Inventory:
        names = ["android_permission_intel_dev", "erebus_threat_intel_dev", "scytaledroid_core_dev", "random_dev"]

    monkeypatch.setattr("mercury.database.discovery.discover_for_planning", lambda live: Inventory())
    assert resolve_development_backup_sources(live=True) == [
        "android_permission_intel_dev", "erebus_threat_intel_dev", "scytaledroid_core_dev",
    ]


def test_development_verification_only_checks_written_results(monkeypatch, tmp_path) -> None:
    from mercury.backup.batch_runner import BackupBatchResult, verify_development_backup_batch
    from mercury.backup.backup_runner import BackupExecutionResult

    written = BackupExecutionResult(database="erebus_threat_intel_dev", backup_kind="full", dry_run=False, executed=True, backup_directory="x", backup_directory_path=str(tmp_path))
    skipped = BackupExecutionResult(database="scytaledroid_core_dev", backup_kind="full", dry_run=True, executed=False, backup_directory="y")
    class Verification:
        verified = True
        issues: list[str] = []
    monkeypatch.setattr("mercury.backup.verification.verify_backup_directory", lambda *args, **kwargs: Verification())
    summary = verify_development_backup_batch(BackupBatchResult(backup_kind="full", execute=True, results=[written, skipped]))
    assert summary.verified == 1
    assert summary.failed == 0


def test_explicit_development_verification_keeps_default_policy_strict(tmp_path) -> None:
    from mercury.backup.verification import verify_backup_artifacts
    # An absent artifact still proves the role gate changed only when explicitly requested.
    default = verify_backup_artifacts(tmp_path / "erebus_threat_intel_dev", database="erebus_threat_intel_dev")
    explicit = verify_backup_artifacts(
        tmp_path / "erebus_threat_intel_dev", database="erebus_threat_intel_dev", allow_development_backup=True
    )
    assert default.role_ok is False
    assert explicit.role_ok is True
