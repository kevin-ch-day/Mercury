from pathlib import Path

import pytest

from mercury.storage.host_maintenance import HostMaintenanceState, load_host_maintenance, save_host_maintenance


def _local_config(path: Path) -> None:
    path.write_text('[mercury]\ndry_run = false\n\n[storage]\nmigration_state = "verified"\n', encoding='utf-8')


def test_cutover_adds_missing_primary_role_and_updates_host_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mercury.storage.cutover_approve as cutover

    config_path = tmp_path / 'local.toml'
    host_path = tmp_path / 'host.json'
    _local_config(config_path)
    save_host_maintenance(HostMaintenanceState(
        storage_availability='detaching', writes_allowed=False, active_write_role='none',
        source_detach_preparation=True, destination_rehearsal_active=True,
        destination_rehearsal_in_progress=True,
    ), path=host_path)
    generation = type('Generation', (), {'generation': 'generation-1'})()
    monkeypatch.setattr(cutover, 'build_cutover_readiness', lambda **_kwargs: type('Ready', (), {'ready': True})())
    monkeypatch.setattr(cutover, 'build_usb_generation', lambda **_kwargs: generation)
    monkeypatch.setattr(cutover, 'read_verified_generation', lambda **_kwargs: 'generation-1')
    monkeypatch.setattr(cutover, 'load_host_maintenance', lambda: load_host_maintenance(host_path))
    monkeypatch.setattr(cutover, 'save_host_maintenance', lambda state: save_host_maintenance(state, host_path))
    monkeypatch.setattr(cutover, 'write_immutable_receipt', lambda *_args, **_kwargs: tmp_path / 'receipt.json')
    monkeypatch.setattr(cutover, 'append_transition_ledger', lambda *_args, **_kwargs: tmp_path / 'ledger.jsonl')
    monkeypatch.setattr(cutover, 'detect_active_operations', lambda: [])
    backup = cutover.approve_hdd_writer_cutover(confirmation=cutover.CONFIRMATION, local_config=config_path)
    assert backup.exists()
    content = config_path.read_text(encoding='utf-8')
    assert 'active_write_role = "primary"' in content
    assert 'migration_state = "cutover_complete"' in content
    host = load_host_maintenance(host_path)
    assert host.writes_allowed is True
    assert host.active_write_role == 'primary'
    assert host.destination_rehearsal_in_progress is False


def test_cutover_refuses_active_operation_before_mutating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mercury.storage.cutover_approve as cutover

    config_path = tmp_path / 'local.toml'
    _local_config(config_path)
    monkeypatch.setattr(cutover, 'build_cutover_readiness', lambda **_kwargs: type('Ready', (), {'ready': True})())
    monkeypatch.setattr(cutover, 'build_usb_generation', lambda **_kwargs: type('Generation', (), {'generation': 'generation-1'})())
    monkeypatch.setattr(cutover, 'read_verified_generation', lambda **_kwargs: 'generation-1')
    monkeypatch.setattr(cutover, 'load_host_maintenance', lambda: HostMaintenanceState(
        storage_availability='detaching', writes_allowed=False, active_write_role='none'
    ))
    monkeypatch.setattr(cutover, 'detect_active_operations', lambda: ['restore'])
    with pytest.raises(ValueError, match='Active operation blocks cutover'):
        cutover.approve_hdd_writer_cutover(confirmation=cutover.CONFIRMATION, local_config=config_path)
    assert config_path.read_text(encoding='utf-8') == '[mercury]\ndry_run = false\n\n[storage]\nmigration_state = "verified"\n'
