"""Read-only health checks with missing or damaged operator metadata."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.backup import health
from mercury.backup.status import RestoreCheckLedgerRecord


@pytest.fixture
def health_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    mount = SimpleNamespace(mount_path=tmp_path, filesystem_uuid="fixture")
    monkeypatch.setattr(health, "load_storage_config", lambda: SimpleNamespace(active_write_root=mount))
    monkeypatch.setattr(health, "require_disk", lambda *_: None)
    monkeypatch.setattr(health, "_unit_state", dict)
    monkeypatch.setattr(health, "ACTIVE_BACKUP_SOURCE_DATABASES", [])
    monkeypatch.setattr(health, "load_repo_definitions", lambda: [
        SimpleNamespace(key="device", path=Path.home() / "Projects/Noctivar/android-device")
    ])
    return tmp_path


@pytest.mark.parametrize(
    ("receipt_status", "rows", "expected_status", "successful"),
    [
        (None, None, "unavailable", False),
        ("complete", [None], "invalid", False),
        ("complete", [{"key": "device"}, {"key": "device"}], "invalid", False),
        ("complete", [{"key": "device", "synced": "false"}], "invalid", False),
        ("partial", [{"key": "device", "synced": True}], "partial", False),
        (None, [{"key": "device", "synced": True}], "legacy_receipt", True),
        ("complete", [{"key": "device", "synced": True, "snapshot": "bad"}], "complete", True),
    ],
)
def test_repository_health_reports_receipt_evidence(
    health_mount: Path, receipt_status, rows, expected_status, successful,
) -> None:
    stamp = "2026-05-30T10:00:00Z"
    if rows is not None:
        receipt = health_mount / "mercury_repo_clones/offline_repo_sync_receipt.json"
        receipt.parent.mkdir()
        payload = {"repositories": rows, "recorded_at_utc": stamp}
        if receipt_status is not None:
            payload["status"] = receipt_status
        receipt.write_text(json.dumps(payload))

    report = health.build_health()
    assert report["repository_receipt_status"] == expected_status
    assert report["latest_successful_repository_receipt"] == (stamp if successful else None)
    assert report["repositories"]["android-device"]["snapshot_saved_files"] is None
    if rows and isinstance(rows[0], dict) and rows[0].get("synced") == "false":
        assert not report["repositories"]["android-device"]["recorded_synced"]


def test_health_reads_repository_run_state_once(health_mount: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = health_mount / "mercury_repo_clones/generations/run/STATE.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"status": "partial", "path": "receipt path"}))
    original = health.read_json
    reads = []

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(health, "read_json", read)
    result = health.build_health()
    assert reads.count(state) == 1
    assert result["latest_failed_or_partial_repository_generations"] == [
        {"status": "partial", "path": str(state)}
    ]


def test_health_reports_next_actions_for_exact_backups(health_mount: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = 'scytaledroid_core_prod'
    stamp = datetime.now(UTC)
    directory = health_mount / 'mercury_backups' / stamp.date().isoformat() / source
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_text(json.dumps({
        'backup_id': 'full-fixture', 'database': source, 'backup_kind': 'full',
        'created_at': stamp.isoformat(), 'dump_file': 'fixture.sql.gz',
        'source_role': 'production', 'verified': True,
    }))
    monkeypatch.setattr(health, 'ACTIVE_BACKUP_SOURCE_DATABASES', ['noctivar', source])
    records = {'full-fixture': RestoreCheckLedgerRecord(
        database='erebus_threat_intel_prod', backup_id='full-fixture', status='passed',
    )}
    monkeypatch.setattr(health, 'latest_restore_check_by_backup_id', lambda: records)

    databases = health.build_health()['databases']
    assert databases['noctivar']['next_action'] == 'review_complete_backup_access'
    assert databases[source]['recorded_restore_check_status'] is None
    assert databases[source]['next_action'] == 'run_restore_check'
    records['full-fixture'] = records['full-fixture'].model_copy(update={'database': source, 'status': 'failed'})
    assert health.build_health()['databases'][source]['next_action'] == 'investigate_restore_check_failure'
    records['full-fixture'] = records['full-fixture'].model_copy(update={'status': 'passed'})
    assert health.build_health()['databases'][source]['next_action'] is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
def test_health_does_not_wait_on_nonregular_receipt_or_lock(tmp_path: Path) -> None:
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)
    result = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; import sys; "
         "from mercury.backup.health import read_json, _lock_active; "
         "p = Path(sys.argv[1]); print(read_json(p), _lock_active(p))", str(pipe)],
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert result.stdout.strip() == "{} None"
