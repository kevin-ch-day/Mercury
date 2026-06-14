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
            verified_source_count=2,
            missing_source_count=0,
            failed_source_count=0,
            stale_source_count=0,
            unknown_freshness_source_count=0,
            source_freshness={
                "android_permission_intel": "fresh",
                "erebus_threat_intel_prod": "fresh",
            },
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
    monkeypatch.setattr("mercury.transfer.bundle.socket.gethostname", lambda: "fedora")

    bundle = build_transfer_bundle(live=True)
    assert bundle.ready_sync_pairs == 1
    assert bundle.blocked_sync_pairs == 0
    assert bundle.host == "fedora"
    assert len(bundle.database_entries) == 2
    assert bundle.database_entries[0].verified is True
    assert bundle.verified_source_count == 2
    assert bundle.stale_source_count == 0
    assert len(bundle.repo_entries) == 1
    assert bundle.repo_entries[0].repo_name == "Mercury"
    assert bundle.repo_entries[0].warning is not None
    assert bundle.warnings


def test_write_transfer_bundle_writes_manifest_and_runbook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    usb_root = tmp_path / "usb"
    state_root = tmp_path / "state"
    manifest_path = usb_root / "mercury_manifests" / "transfer_manifest.json"
    runbook_path = usb_root / "mercury_runbooks" / "transfer_runbook.md"
    bundle = TransferBundle(
        generated_at="2026-06-09T00:00:00+00:00",
        host="fedora",
        mode="live",
        backup_root=str(usb_root / "mercury_backups"),
        required_usb_mount=str(usb_root),
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
                bundle_path=str(usb_root / "mercury_repo_backups" / "mercury.bundle"),
                repo_manifest_path=str(usb_root / "mercury_manifests" / "mercury.repo_manifest.json"),
                repo_runbook_path=str(usb_root / "mercury_runbooks" / "mercury.restore.md"),
                bundle_verified=True,
                bundle_size_bytes=123,
                warning="Repository was dirty at bundle time. Git bundles contain committed history only; uncommitted changes are not included.",
            )
        ],
        ready_sync_pairs=2,
        blocked_sync_pairs=0,
        dirty_repo_names=["Mercury"],
        warnings=["Dirty repos not fully captured by Git bundles: Mercury"],
        transfer_manifest_path=str(manifest_path),
        transfer_runbook_path=str(runbook_path),
        latest_transfer_manifest_path=str(manifest_path),
        latest_transfer_runbook_path=str(runbook_path),
    )
    monkeypatch.setattr("mercury.core.usb_mount.resolve_usb_mount", lambda **kwargs: usb_root)
    monkeypatch.setattr("mercury.core.usb_mount.usb_mount_is_active", lambda path, **kwargs: True)
    monkeypatch.setattr("mercury.state.ledger.resolve_state_root", lambda policy=None: state_root)

    written = write_transfer_bundle(bundle)
    assert Path(written.transfer_manifest_path).exists()
    assert Path(written.transfer_runbook_path).exists()
    manifest = json.loads(Path(written.transfer_manifest_path).read_text(encoding="utf-8"))
    assert manifest["database_entries"][0]["database"] == "android_permission_intel"
    assert manifest["host"] == "fedora"
    assert manifest["repo_entries"][0]["bundle_verified"] is True
    runbook = Path(written.transfer_runbook_path).read_text(encoding="utf-8")
    assert "Mercury transfer runbook" in runbook
    assert "Actual sync: deferred" in runbook
    assert "Dirty repos not fully captured by Git bundles: Mercury" in runbook
    assert "Git bundles include committed history only" in runbook
    transfer_csv = (state_root / "transfer_packages.csv").read_text(encoding="utf-8")
    assert "transfer_manifest.json" in transfer_csv
    operations = (state_root / "operations.jsonl").read_text(encoding="utf-8")
    assert "transfer_bundle_written" in operations


def test_print_transfer_bundle_uses_planned_and_written_labels(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.transfer.terminal import print_transfer_bundle
    from mercury.state.summary import StateSummary

    bundle = TransferBundle(
        generated_at="2026-06-09T00:00:00+00:00",
        host="fedora",
        mode="live",
        backup_root="/mnt/MERCURY_DATA_USB/mercury_backups",
        required_usb_mount="/mnt/MERCURY_DATA_USB",
        manifest_dir="/mnt/MERCURY_DATA_USB/mercury_manifests",
        runbook_dir="/mnt/MERCURY_DATA_USB/mercury_runbooks",
        database_entries=[],
        repo_entries=[],
        ready_sync_pairs=2,
        blocked_sync_pairs=0,
        dirty_repo_names=[],
        warnings=[],
        transfer_manifest_path="/mnt/MERCURY_DATA_USB/mercury_manifests/transfer_manifest.json",
        transfer_runbook_path="/mnt/MERCURY_DATA_USB/mercury_runbooks/transfer_runbook.md",
        latest_transfer_manifest_path="/mnt/MERCURY_DATA_USB/mercury_manifests/transfer_manifest_prev.json",
        latest_transfer_runbook_path="/mnt/MERCURY_DATA_USB/mercury_runbooks/transfer_runbook_prev.md",
    )
    import mercury.transfer.terminal as terminal_mod

    terminal_mod.build_state_summary = lambda: StateSummary(
        state_root=Path("/mnt/MERCURY_DATA_USB/mercury_state"),
        source="usb",
        operations=7,
        database_backup_rows=3,
        database_bundle_rows=0,
        repo_bundle_rows=2,
        transfer_package_rows=1,
        sync_event_rows=0,
    )

    print_transfer_bundle(bundle, executed=False)
    planned_out = capsys.readouterr().out
    assert "Latest transfer manifest:" in planned_out
    assert "Latest transfer runbook:" in planned_out
    assert "Database package" in planned_out
    assert "Repository package" in planned_out
    assert "Actual sync" in planned_out
    assert "State root" in planned_out
    assert "State ops" in planned_out

    print_transfer_bundle(bundle, executed=True)
    written_out = capsys.readouterr().out
    assert "Transfer manifest written:" in written_out
    assert "Transfer runbook written:" in written_out
    assert "Transfer package written to USB." in written_out


def test_print_transfer_bundle_stale_database_package_shows_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.transfer.terminal import print_transfer_bundle

    bundle = TransferBundle(
        generated_at="2026-06-09T00:00:00+00:00",
        host="fedora",
        mode="live",
        backup_root="/mnt/MERCURY_DATA_USB/mercury_backups",
        required_usb_mount="/mnt/MERCURY_DATA_USB",
        manifest_dir="/mnt/MERCURY_DATA_USB/mercury_manifests",
        runbook_dir="/mnt/MERCURY_DATA_USB/mercury_runbooks",
        database_entries=[
            TransferDatabaseEntry(
                database="erebus_threat_intel_prod",
                source_role="production source",
                verified=True,
                freshness="stale",
                backup_id="erebus-full-1",
                backup_directory="/mnt/MERCURY_DATA_USB/mercury_backups/erebus",
            )
        ],
        repo_entries=[],
        verified_source_count=1,
        missing_source_count=0,
        failed_source_count=0,
        stale_source_count=1,
        unknown_freshness_source_count=0,
        ready_sync_pairs=0,
        blocked_sync_pairs=1,
        dirty_repo_names=[],
        warnings=[],
        transfer_manifest_path="/mnt/MERCURY_DATA_USB/mercury_manifests/transfer_manifest.json",
        transfer_runbook_path="/mnt/MERCURY_DATA_USB/mercury_runbooks/transfer_runbook.md",
    )
    print_transfer_bundle(bundle, executed=False)
    out = capsys.readouterr().out
    assert "Database package: complete with warnings" in out
    assert "1 stale" in out
    assert "handoff should wait for fresh full backups" in out
    assert "FRESH" in out
    assert "Stale" in out
