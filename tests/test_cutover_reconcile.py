from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.core.storage_roles import MigrationState, StorageWriteRole
from mercury.storage.host_maintenance import HostMaintenanceState, load_host_maintenance, save_host_maintenance


def test_completed_cutover_reconcile_repairs_only_stale_host_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mercury.storage.cutover_reconcile as reconcile

    package = tmp_path / "destination_rehearsal_20260725T000000Z"
    package.mkdir()
    (package / "package_receipt.json").write_text(json.dumps({
        "package_id": package.name, "verification_status": "DESTINATION_PACKAGE_VERIFIED",
    }))
    host_path = tmp_path / "host.json"
    before = HostMaintenanceState(
        storage_availability="detaching", writes_allowed=False, active_write_role="none",
        destination_rehearsal_active=True, destination_rehearsal_in_progress=True,
    )
    save_host_maintenance(before, path=host_path)
    config = SimpleNamespace(
        active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
        primary=SimpleNamespace(filesystem_uuid="hdd", control_dir=tmp_path / "control"),
        legacy=SimpleNamespace(filesystem_uuid="usb"),
    )
    monkeypatch.setattr(reconcile, "load_storage_config", lambda **_kwargs: config)
    monkeypatch.setattr(reconcile, "build_storage_status_report", lambda: SimpleNamespace(primary=SimpleNamespace(validation=SimpleNamespace(ok=True))))
    monkeypatch.setattr(reconcile, "read_cutover_receipt", lambda **_kwargs: {"hdd_uuid": "hdd", "usb_uuid": "usb", "cutover_verified_hdd_generation": "gen"})
    monkeypatch.setattr(reconcile, "read_verified_generation", lambda **_kwargs: "gen")
    monkeypatch.setattr("mercury.storage.detach_wizard.verify_package_manifest", lambda _root: [])
    monkeypatch.setattr(reconcile, "detect_active_operations", lambda: [])
    monkeypatch.setattr(reconcile, "load_host_maintenance", lambda: load_host_maintenance(host_path))
    monkeypatch.setattr(reconcile, "save_host_maintenance", lambda state: save_host_maintenance(state, host_path))
    receipt_path = tmp_path / "receipt.json"
    monkeypatch.setattr(reconcile, "write_immutable_receipt", lambda _name, _payload, **_kwargs: receipt_path)
    events: list[dict] = []
    monkeypatch.setattr(reconcile, "append_transition_ledger", lambda event: events.append(event))

    result = reconcile.reconcile_completed_hdd_cutover(
        confirmation=reconcile.CONFIRMATION, package_root=package, package_id=package.name,
    )
    state = load_host_maintenance(host_path)
    assert result == receipt_path
    assert state.writes_allowed is True
    assert state.active_write_role == "primary"
    assert state.destination_rehearsal_in_progress is False
    assert state.package_id == package.name
    assert events[0]["transition"] == "completed_hdd_cutover_host_maintenance_reconciliation"


def test_reconcile_refuses_active_operation_without_mutating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mercury.storage.cutover_reconcile as reconcile

    package = tmp_path / "destination_rehearsal_20260725T000000Z"
    package.mkdir()
    (package / "package_receipt.json").write_text(json.dumps({
        "package_id": package.name, "verification_status": "DESTINATION_PACKAGE_VERIFIED",
    }))
    before = HostMaintenanceState(storage_availability="detaching", writes_allowed=False, active_write_role="none")
    config = SimpleNamespace(
        active_write_role=StorageWriteRole.PRIMARY, migration_state=MigrationState.CUTOVER_COMPLETE,
        primary=SimpleNamespace(filesystem_uuid="hdd", control_dir=tmp_path / "control"), legacy=SimpleNamespace(filesystem_uuid="usb"),
    )
    monkeypatch.setattr(reconcile, "load_storage_config", lambda **_kwargs: config)
    monkeypatch.setattr(reconcile, "build_storage_status_report", lambda: SimpleNamespace(primary=SimpleNamespace(validation=SimpleNamespace(ok=True))))
    monkeypatch.setattr(reconcile, "read_cutover_receipt", lambda **_kwargs: {"hdd_uuid": "hdd", "usb_uuid": "usb", "cutover_verified_hdd_generation": "gen"})
    monkeypatch.setattr(reconcile, "read_verified_generation", lambda **_kwargs: "gen")
    monkeypatch.setattr("mercury.storage.detach_wizard.verify_package_manifest", lambda _root: [])
    monkeypatch.setattr(reconcile, "detect_active_operations", lambda: ["restore"])
    monkeypatch.setattr(reconcile, "load_host_maintenance", lambda: before)
    with pytest.raises(ValueError, match="Active operation"):
        reconcile.reconcile_completed_hdd_cutover(
            confirmation=reconcile.CONFIRMATION, package_root=package, package_id=package.name,
        )
    assert before.writes_allowed is False
