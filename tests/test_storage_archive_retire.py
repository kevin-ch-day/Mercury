from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig
from mercury.storage.host_maintenance import HostMaintenanceState


def _config(tmp_path: Path) -> StorageConfig:
    primary = tmp_path / "hdd"
    primary.mkdir()
    return StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "HDD", primary, "hdd", "ext4", True),
        legacy=StorageRootConfig("legacy", StorageRootRole.LEGACY_ARCHIVE, "USB", tmp_path / "usb", "usb", "ext4", False),
        active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
    )


def _archive() -> dict:
    return {
        "usb_uuid": "usb",
        "final_usb_archive_generation": "generation",
        "manifest_sha256": "manifest",
        "relative_path_manifest": [
            {"path": "mercury_manifests", "kind": "dir", "size": None},
            {"path": "mercury_manifests/historical.json", "kind": "file", "size": 4},
        ],
    }


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import mercury.storage.archive_retire as retire

    config = _config(tmp_path)
    historical = config.primary.mount_path / "mercury_manifests"
    historical.mkdir()
    (historical / "historical.json").write_text("data")
    monkeypatch.setattr(retire, "build_storage_status_report", lambda: SimpleNamespace(
        primary=SimpleNamespace(validation=SimpleNamespace(ok=True, code=SimpleNamespace(value="ok"))),
        legacy=SimpleNamespace(is_offline_archive=True),
    ))
    monkeypatch.setattr(retire, "detect_active_operations", lambda: [])
    monkeypatch.setattr(retire, "load_host_maintenance", lambda: HostMaintenanceState(
        writes_allowed=True, active_write_role="primary",
    ))
    monkeypatch.setattr(retire, "read_cutover_receipt", lambda **_kwargs: {
        "hdd_uuid": "hdd", "usb_uuid": "usb", "cutover_verified_hdd_generation": "generation",
        "final_usb_archive_generation": "generation",
    })
    monkeypatch.setattr(retire, "read_verified_generation", lambda **_kwargs: "generation")
    monkeypatch.setattr(retire, "read_archive_receipt", lambda **_kwargs: _archive())
    events: list[dict] = []
    monkeypatch.setattr(retire, "append_transition_ledger", lambda event: events.append(event))
    return retire, config, events


def test_retire_records_evidence_without_changing_writer_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, events = _prepare(tmp_path, monkeypatch)
    result = retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    assert result == config.primary.control_dir / retire.RETIREMENT_RECEIPT_FILE
    assert result.is_file()
    assert events[0]["transition"] == "legacy_usb_archive_retirement"
    assert events[0]["usb_data_changed"] is False
    assert config.active_write_role == StorageWriteRole.PRIMARY
    assert config.legacy.writable is False


def test_retire_refuses_bad_evidence_or_missing_historical_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, _events = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(retire, "read_cutover_receipt", lambda **_kwargs: {
        "hdd_uuid": "hdd", "usb_uuid": "wrong", "cutover_verified_hdd_generation": "generation",
        "final_usb_archive_generation": "generation",
    })
    with pytest.raises(ValueError, match="UUID"):
        retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    monkeypatch.setattr(retire, "read_cutover_receipt", lambda **_kwargs: {
        "hdd_uuid": "hdd", "usb_uuid": "usb", "cutover_verified_hdd_generation": "generation",
        "final_usb_archive_generation": "generation",
    })
    (config.primary.mount_path / "mercury_manifests" / "historical.json").unlink()
    with pytest.raises(ValueError, match="missing or changed"):
        retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)


def test_retire_refuses_active_operation_and_repeated_retirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, _events = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(retire, "detect_active_operations", lambda: ["restore"])
    with pytest.raises(ValueError, match="Active operation"):
        retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    monkeypatch.setattr(retire, "detect_active_operations", lambda: [])
    retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)
    with pytest.raises(ValueError, match="already recorded"):
        retire.retire_legacy_usb_archive(confirmation=retire.CONFIRMATION, config=config)


def test_retire_requires_exact_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retire, config, _events = _prepare(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="RETIRE LEGACY USB"):
        retire.retire_legacy_usb_archive(confirmation="retire", config=config)
