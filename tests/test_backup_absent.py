"""Tests for absent-on-server backup sources and handoff scoring."""

from __future__ import annotations

from pathlib import Path

from mercury.backup.status import BackupStatusEntry, build_backup_status_report
from mercury.core.handoff_status import database_bundle_package_status
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.paths import REPO_ROOT


def test_absent_source_does_not_count_as_missing(monkeypatch, tmp_path: Path) -> None:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "mercury_backups",
        usb_mount=tmp_path,
        allow_unsafe_backup_root=True,
    )
    (tmp_path / "mercury_backups").mkdir(parents=True)

    monkeypatch.setattr(
        "mercury.backup.status.select_batch_sources",
        lambda **kwargs: [
            "erebus_threat_intel_prod",
            "obsidiandroid_core_prod",
        ],
    )
    monkeypatch.setattr(
        "mercury.backup.status.find_latest_backup_directory",
        lambda root, database: None,
    )
    monkeypatch.setattr(
        "mercury.backup.status._live_server_database_names",
        lambda **kwargs: {"erebus_threat_intel_prod"},
    )

    report = build_backup_status_report(live=True, policy=policy)
    assert report.missing_count == 1
    assert report.absent_count == 1
    by_name = {entry.database: entry for entry in report.entries}
    assert by_name["obsidiandroid_core_prod"].protection_status == "absent"
    assert by_name["erebus_threat_intel_prod"].protection_status == "missing"
    assert any("do not block handoff" in warning for warning in report.warnings)


def test_package_status_complete_with_absent_only() -> None:
    assert (
        database_bundle_package_status(
            source_count=4,
            verified_count=3,
            missing_count=0,
            failed_count=0,
            absent_count=1,
        )
        == "complete with warnings"
    )
