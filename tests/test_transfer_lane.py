"""Tests for combined Mercury transfer manifest and runbook."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.backup.on_disk_index import OnDiskBackupList, OnDiskBackupRecord
from mercury.database.backup_planning import BackupPlanDryRun
from mercury.reporting.protection import ProtectionReport
from mercury.repo.status import RepoStatus
from mercury.sync.readiness import SyncReadinessEntry, SyncReadinessReport
from mercury.transfer.bundle import (
    TransferBundle,
    TransferDatabaseEntry,
    TransferRepoEntry,
    build_transfer_bundle,
    write_transfer_bundle,
)


def test_build_transfer_bundle_aggregates_database_and_repo_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mercury.transfer.bundle.load_execution_policy",
        lambda: type("Policy", (), {"backup_root": tmp_path / "backups"})(),
    )
    monkeypatch.setattr(
        "mercury.transfer.bundle.load_repo_bundle_settings",
        lambda: type(
            "Settings",
            (),
            {
                "manifest_dir": tmp_path / "manifests",
                "runbook_dir": tmp_path / "runbooks",
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.transfer.bundle.build_protection_report",
        lambda live=False, probe_database=False: ProtectionReport(
            generated_at="2026-06-09T00:00:00+00:00",
            mode="live" if live else "seed",
            connection="connected",
            backup_plan=BackupPlanDryRun(),
            inventory_count=5,
            protected=[
                "android_permission_intel",
                "erebus_threat_intel_prod",
            ],
            shared_authority=["android_permission_intel"],
        ),
    )
    monkeypatch.setattr(
        "mercury.transfer.bundle.build_sync_readiness_report",
        lambda live=False: SyncReadinessReport(
            mode="live",
            backup_root=str(tmp_path / "backups"),
            entries=[
                SyncReadinessEntry(
                    prod="erebus_threat_intel_prod",
                    expected_dev="erebus_threat_intel_dev",
                    dev_listed=True,
                    ready_for_sync_planning=True,
                )
            ],
            ready_count=1,
            blocked_count=0,
        ),
    )
    monkeypatch.setattr(
        "mercury.transfer.bundle.build_on_disk_backup_list",
        lambda _root: OnDiskBackupList(
            backup_root=str(tmp_path / "backups"),
            records=[
                OnDiskBackupRecord(
                    database="android_permission_intel",
                    backup_kind="full",
                    backup_id="android-full-1",
                    directory=str(tmp_path / "backups/android"),
                    verified=True,
                ),
                OnDiskBackupRecord(
                    database="erebus_threat_intel_prod",
                    backup_kind="full",
                    backup_id="erebus-full-1",
                    directory=str(tmp_path / "backups/erebus"),
                    verified=True,
                ),
            ],
        ),
    )
    monkeypatch.setattr(
        "mercury.transfer.bundle.verify_backup_artifacts",
        lambda path, database=None: type(
            "Result",
            (),
            {"verified": True, "backup_kind": "full"},
        )(),
    )
    monkeypatch.setattr("mercury.transfer.bundle.load_repo_definitions", lambda: [])
    monkeypatch.setattr(
        "mercury.transfer.bundle.inspect_repositories",
        lambda _repos: [
            RepoStatus(
                key="mercury",
                display_name="Mercury",
                path=tmp_path / "Mercury",
                branch="main",
                commit="abc123def456",
                remote_url="https://example/Mercury.git",
                dirty=True,
                untracked_count=2,
            )
        ],
    )

    bundle = build_transfer_bundle(live=True)
    assert bundle.ready_sync_pairs == 1
    assert bundle.blocked_sync_pairs == 0
    assert len(bundle.database_entries) == 2
    assert bundle.database_entries[0].verified is True
    assert len(bundle.repo_entries) == 1
    assert bundle.repo_entries[0].repo_name == "Mercury"


def test_write_transfer_bundle_writes_manifest_and_runbook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    usb_root = tmp_path / "usb"
    manifest_path = usb_root / "mercury_manifests" / "transfer_manifest.json"
    runbook_path = usb_root / "mercury_runbooks" / "transfer_runbook.md"
    bundle = TransferBundle(
        generated_at="2026-06-09T00:00:00+00:00",
        mode="live",
        backup_root=str(usb_root / "mercury_backups"),
        manifest_dir=str(usb_root / "mercury_manifests"),
        runbook_dir=str(usb_root / "mercury_runbooks"),
        database_entries=[
            TransferDatabaseEntry(
                database="android_permission_intel",
                source_role="shared authority",
                verified=True,
                backup_id="android-full-1",
                backup_directory=str(usb_root / "mercury_backups/android"),
            )
        ],
        repo_entries=[
            TransferRepoEntry(
                repo_key="mercury",
                repo_name="Mercury",
                repo_path="/repos/Mercury",
                branch="main",
                commit="abc123def456",
                remote_url="https://example/Mercury.git",
                dirty=True,
                untracked_count=2,
            )
        ],
        ready_sync_pairs=2,
        blocked_sync_pairs=0,
        transfer_manifest_path=str(manifest_path),
        transfer_runbook_path=str(runbook_path),
    )
    monkeypatch.setattr("mercury.transfer.bundle.REQUIRED_BACKUP_MOUNT", usb_root)
    monkeypatch.setattr(Path, "is_mount", lambda self: self == usb_root)

    written = write_transfer_bundle(bundle)
    assert Path(written.transfer_manifest_path).exists()
    assert Path(written.transfer_runbook_path).exists()
    manifest = json.loads(Path(written.transfer_manifest_path).read_text(encoding="utf-8"))
    assert manifest["database_entries"][0]["database"] == "android_permission_intel"
    runbook = Path(written.transfer_runbook_path).read_text(encoding="utf-8")
    assert "Mercury transfer runbook" in runbook
    assert "Git bundles include committed history only" in runbook
