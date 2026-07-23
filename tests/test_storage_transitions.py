"""Phase 1 storage transitions + recoverable/strong backup writer restoration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    assert_not_live_mercury_path,
    load_host_maintenance,
    save_host_maintenance,
    writes_allowed,
)
from mercury.storage.operation_availability import (
    AvailabilityClassification,
    OperationStatus,
    assess_operation_availability,
    ensure_backup_writes_available,
    format_strong_prompt,
)
from mercury.storage.transitions import (
    RESTORE_SOURCE_WRITER_PHRASE,
    TransitionStatus,
    append_transition_ledger,
    default_transition_ledger_path,
    disable_writes,
    prepare_disconnect,
    restore_source_writer,
)


@pytest.fixture
def hermetic_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dedicated temporary Mercury tree for transition tests."""
    host = tmp_path / "host_maintenance.json"
    ledger = tmp_path / "transition_ledger.jsonl"
    operator = tmp_path / "operator_root"
    backups = tmp_path / "backups"
    logs = tmp_path / "logs"
    for path in (operator, backups, logs):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(host))
    monkeypatch.setenv("MERCURY_TRANSITION_LEDGER_PATH", str(ledger))
    monkeypatch.setenv("MERCURY_BACKUP_ROOT", str(backups))
    monkeypatch.setenv("MERCURY_OPERATOR_ROOT", str(operator))
    monkeypatch.setenv("MERCURY_LOG_ROOT", str(logs))
    monkeypatch.setenv("MERCURY_LOG_DIR", str(logs))
    monkeypatch.setenv("MERCURY_TEST_ISOLATION", "1")
    monkeypatch.setenv("MERCURY_EVENT_ENVIRONMENT", "test")
    monkeypatch.delenv("MERCURY_ACTIVE_OPERATION", raising=False)
    return tmp_path


@pytest.fixture
def host_path(hermetic_root: Path) -> Path:
    return hermetic_root / "host_maintenance.json"


def _mounted_identity(**kwargs):
    base = dict(
        label="MERCURY_DATA_V2",
        model="WDC WD10JDRW",
        uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        fstype="ext4",
        mountpoint="/mnt/MERCURY_DATA_V2",
        partition_device="/dev/mock1",
        parent_device="/dev/mock",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _resolve_ok(**kwargs):
    return SimpleNamespace(identity=_mounted_identity(**kwargs), errors=[])


def _resolve_absent(**_kwargs):
    return SimpleNamespace(identity=None, errors=["UUID not found"])


def _resolve_bad_uuid(**_kwargs):
    return SimpleNamespace(
        identity=_mounted_identity(uuid="00000000-0000-0000-0000-000000000000"),
        errors=["uuid mismatch"],
    )


def _patch_healthy_mount(monkeypatch: pytest.MonkeyPatch, resolve=_resolve_ok) -> None:
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: resolve(),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.detect_desktop_automount",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "mercury.storage.transitions._probe_mount_mode",
        lambda *_a, **_k: "read-write",
    )


def test_isolation_refuses_live_host_paths(hermetic_root: Path) -> None:
    live = Path.home() / ".local" / "share" / "mercury" / "host_maintenance.json"
    with pytest.raises(RuntimeError, match="TEST ISOLATION"):
        assert_not_live_mercury_path(live, purpose="test")
    with pytest.raises(RuntimeError, match="TEST ISOLATION"):
        assert_not_live_mercury_path("/mnt/MERCURY_DATA_V2/foo", purpose="test")
    # Hermetic ledger is under tmp — allowed.
    append_transition_ledger({"transition": "noop", "result": "SUCCESS"})
    ledger = default_transition_ledger_path()
    assert str(hermetic_root) in str(ledger)
    assert ledger.is_file()


def test_backup_available_when_writes_enabled(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    avail = assess_operation_availability("database_backup")
    assert avail.available is True
    assert avail.classification == AvailabilityClassification.AVAILABLE


def test_detach_prep_only_is_recoverable(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            destination_rehearsal_in_progress=False,
            notes="Preparing for safe disconnect",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    avail = assess_operation_availability("database_backup")
    assert avail.is_recoverable
    assert avail.confirmation_type.value == "yes_no"


def test_live_like_destination_rehearsal_is_strong(host_path: Path, monkeypatch) -> None:
    """Mirrors current live host: detaching + verified package + rehearsal active."""
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            destination_rehearsal_in_progress=True,
            destination_rehearsal_planned=True,
            package_id="destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            notes="Mercury HDD detach in progress; destination cutover is NOT complete.",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    avail = assess_operation_availability("database_backup")
    assert avail.is_strong
    assert avail.confirmation_phrase == RESTORE_SOURCE_WRITER_PHRASE
    text = format_strong_prompt(avail)
    assert "SOURCE WRITER RESTORE REQUIRES CONFIRMATION" in text
    assert "RESTORE SOURCE WRITER" in text
    assert "Operation unavailable" not in text


def test_migration_lock_requires_exact_phrase(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="attached",
            writes_allowed=False,
            active_write_role="none",
            destination_rehearsal_active=False,
            notes="migration lock active",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    avail = assess_operation_availability("database_backup")
    assert avail.is_strong


def test_package_transfer_active_hard_blocks(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detaching", writes_allowed=False),
        path=host_path,
    )
    monkeypatch.setenv("MERCURY_ACTIVE_OPERATION", "package_transfer")
    _patch_healthy_mount(monkeypatch)
    avail = assess_operation_availability("database_backup")
    assert avail.is_hard_block


def test_user_accepts_restore_and_continue(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            notes="detach preparation",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    lines: list[str] = []
    result = ensure_backup_writes_available(
        interactive=True,
        ask_yes_no=lambda *_a, **_k: True,
        write=lines.append,
    )
    assert result.available is True
    assert result.operation_status == OperationStatus.CONTINUED
    assert result.transition_status == TransitionStatus.SUCCESS
    assert writes_allowed() is True
    host = load_host_maintenance(host_path)
    assert host.storage_availability == "mounted"
    assert host.active_write_role == "primary"
    assert host.source_detach_preparation is False
    assert any("Continuing backup" in line for line in lines)


def test_strong_accept_sets_source_delta(host_path: Path, monkeypatch) -> None:
    package_id = "phase3b_test_package"
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            destination_rehearsal_planned=True,
            package_id=package_id,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    result = ensure_backup_writes_available(
        interactive=True,
        ask_phrase=lambda *_a, **_k: RESTORE_SOURCE_WRITER_PHRASE,
        write=lambda *_a, **_k: None,
    )
    assert result.available is True
    host = load_host_maintenance(host_path)
    assert host.package_id == package_id
    assert host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
    assert host.source_writes_resumed_after_package is True
    assert host.source_delta_relative_to_package_id == package_id
    assert host.source_delta_reason == "operator_restored_source_writer"
    assert host.destination_rehearsal_active is False
    assert host.destination_rehearsal_planned is True
    assert host.source_detach_preparation is False


def test_user_declines_restoration(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            notes="detach preparation",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    before = load_host_maintenance(host_path)
    result = ensure_backup_writes_available(
        interactive=True,
        ask_yes_no=lambda *_a, **_k: False,
        write=lambda *_a, **_k: None,
    )
    assert result.available is False
    assert result.operation_status == OperationStatus.CANCELLED
    assert result.transition_status == TransitionStatus.CANCELLED
    after = load_host_maintenance(host_path)
    assert after.writes_allowed == before.writes_allowed
    assert after.storage_availability == before.storage_availability


def test_backup_continues_exactly_once_after_transition(
    host_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.backup import interactive_menu as backup_menu
    from mercury.database.backup_planning import BackupPlanDryRun

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            notes="detach preparation",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    restore_prompts = 0

    def fake_yes_no(prompt: str, default=False):
        nonlocal restore_prompts
        if "Restore the backup writer" in prompt:
            restore_prompts += 1
            return True
        return False

    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", fake_yes_no)
    calls: list[str] = []

    def fake_batch(*_a, **_k):
        calls.append("batch")
        assert writes_allowed() is True
        return SimpleNamespace(
            executed_count=1,
            refused_count=0,
            dry_run_count=0,
            results=[],
            errors=[],
            sources=["erebus_threat_intel_prod"],
        )

    monkeypatch.setattr(backup_menu, "run_backup_batch", fake_batch)
    monkeypatch.setattr(backup_menu, "print_backup_batch_result", lambda *_a, **_k: None)
    monkeypatch.setattr(backup_menu, "print_batch_small_backup_warnings", lambda *_a, **_k: None)
    monkeypatch.setattr(
        backup_menu,
        "load_execution_policy",
        lambda: SimpleNamespace(backup_root=Path("/tmp"), dry_run=False),
    )
    monkeypatch.setattr(backup_menu, "should_probe_database_status", lambda: False)
    plan = BackupPlanDryRun(backup_sources=["erebus_threat_intel_prod"])
    backup_menu._run_backup(plan)
    assert restore_prompts == 1
    assert calls == ["batch"]


def test_dev_prompt_only_after_successful_transition(
    host_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.backup import interactive_menu as backup_menu
    from mercury.database.backup_planning import BackupPlanDryRun

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            notes="detach preparation",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    prompts: list[str] = []

    def fake_yes_no(prompt: str, default=False):
        prompts.append(prompt)
        if "Restore the backup writer" in prompt:
            return False
        return False

    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", fake_yes_no)
    plan = BackupPlanDryRun(backup_sources=["erebus_threat_intel_prod"])
    backup_menu._run_full_backup(plan)
    assert any("Restore the backup writer" in p for p in prompts)
    assert not any("development databases" in p.lower() for p in prompts)


def test_strong_blocks_dev_prompt_until_phrase(
    host_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.backup import interactive_menu as backup_menu
    from mercury.database.backup_planning import BackupPlanDryRun

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    prompts: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda *_a, **_k: prompts.append("phrase") or "",
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_yes_no",
        lambda prompt, default=False: (_ for _ in ()).throw(
            AssertionError(f"unexpected yes/no: {prompt}")
        ),
    )
    plan = BackupPlanDryRun(backup_sources=["erebus_threat_intel_prod"])
    backup_menu._run_full_backup(plan)
    assert prompts == ["phrase"]


def test_transition_failure_prevents_backup(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=False,
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    monkeypatch.setattr(
        "mercury.storage.operation_availability.restore_source_writer",
        lambda **kwargs: SimpleNamespace(
            ok=False,
            blockers=["simulated failure"],
            status=TransitionStatus.FAILED,
            transition_id="t1",
        ),
    )
    result = ensure_backup_writes_available(
        interactive=True,
        ask_yes_no=lambda *_a, **_k: True,
        write=lambda *_a, **_k: None,
    )
    assert result.available is False
    assert writes_allowed() is False


def test_device_validation_fails_before_state_write(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch, resolve=_resolve_absent)
    before = host_path.read_text(encoding="utf-8") if host_path.exists() else ""
    result = restore_source_writer(require_strong_phrase=False)
    assert result.status == TransitionStatus.HARD_BLOCK
    assert writes_allowed() is False
    after = host_path.read_text(encoding="utf-8") if host_path.exists() else ""
    assert json.loads(after)["writes_allowed"] is False
    assert "writes_allowed" in before or before == ""


def test_atomic_state_write_failure_rolls_back(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=False,
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)

    def boom(state, path=None):
        raise OSError("simulated atomic write failure")

    result = restore_source_writer(require_strong_phrase=False, save_fn=boom)
    assert result.status == TransitionStatus.FAILED
    host = load_host_maintenance(host_path)
    assert host.writes_allowed is False
    assert host.storage_availability == "detaching"


def test_post_validation_rolls_back(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=False,
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    monkeypatch.setattr(
        "mercury.storage.transitions._post_restore_validate",
        lambda state: ["forced validation failure"],
    )
    result = restore_source_writer(operator_intent="test_rollback", require_strong_phrase=False)
    assert result.status == TransitionStatus.ROLLED_BACK
    assert "rolled_back" in result.rollback_information
    host = load_host_maintenance(host_path)
    assert host.writes_allowed is False
    assert host.storage_availability == "detaching"


def test_ledger_write_failure_keeps_restored_writer(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=False,
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)

    def boom_ledger(*_a, **_k):
        raise OSError("ledger unavailable")

    result = restore_source_writer(
        require_strong_phrase=False,
        ledger_fn=boom_ledger,
    )
    assert result.ok
    assert writes_allowed() is True
    assert any("ledger write failed" in w for w in result.warnings)


def test_backup_failure_after_transition_keeps_writer(
    host_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.backup import interactive_menu as backup_menu
    from mercury.database.backup_planning import BackupPlanDryRun

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            notes="detach preparation",
        ),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch)
    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", lambda *_a, **_k: True)

    def boom_batch(*_a, **_k):
        raise RuntimeError("backup failed after restore")

    monkeypatch.setattr(backup_menu, "run_backup_batch", boom_batch)
    monkeypatch.setattr(
        backup_menu,
        "load_execution_policy",
        lambda: SimpleNamespace(backup_root=Path("/tmp"), dry_run=False),
    )
    monkeypatch.setattr(backup_menu, "should_probe_database_status", lambda: False)
    plan = BackupPlanDryRun(backup_sources=["erebus_threat_intel_prod"])
    with pytest.raises(RuntimeError, match="backup failed"):
        backup_menu._run_backup(plan)
    host = load_host_maintenance(host_path)
    assert host.writes_allowed is True
    assert host.storage_availability == "mounted"
    assert host.source_detach_preparation is False


def test_invalid_uuid_hard_blocks(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detaching", writes_allowed=False),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch, resolve=_resolve_bad_uuid)
    avail = assess_operation_availability("database_backup")
    assert avail.is_hard_block
    assert avail.next_action


def test_hdd_absent_hard_blocks(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detached", writes_allowed=False),
        path=host_path,
    )
    _patch_healthy_mount(monkeypatch, resolve=_resolve_absent)
    avail = assess_operation_availability("database_backup")
    assert avail.is_hard_block


def test_read_only_mount_hard_blocks(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detaching", writes_allowed=False),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: _resolve_ok(),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.detect_desktop_automount",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "mercury.storage.transitions._probe_mount_mode",
        lambda *_a, **_k: "read-only",
    )
    avail = assess_operation_availability("database_backup")
    assert avail.is_hard_block


def test_active_detach_operation_hard_blocks(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detaching", writes_allowed=False),
        path=host_path,
    )
    monkeypatch.setenv("MERCURY_ACTIVE_OPERATION", "detach")
    _patch_healthy_mount(monkeypatch)
    avail = assess_operation_availability("database_backup")
    assert avail.is_hard_block


def test_transition_audit_is_host_local(host_path: Path, hermetic_root: Path) -> None:
    ledger = default_transition_ledger_path()
    assert str(hermetic_root) in str(ledger)
    save_host_maintenance(
        HostMaintenanceState(storage_availability="mounted", writes_allowed=True),
        path=host_path,
    )
    disable_writes(operator_intent="test_ledger")
    payload = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["schema_version"] == 1
    assert payload["event_environment"] == "test"
    assert payload["not_hdd_evidence"] is True
    assert payload["governed_hdd_backup_evidence"] is False
    assert "transition_id" in payload
    assert "mercury_commit" in payload


def test_dashboard_intent_aware_next_action() -> None:
    from mercury.storage.hdd_menu_options import dashboard_next_action_short
    from mercury.storage.lifecycle import (
        LIFECYCLE_LABELS,
        MigrationHostRole,
        StorageLifecycleSnapshot,
        StorageLifecycleState,
    )

    snap = StorageLifecycleSnapshot(
        state=StorageLifecycleState.READY_TO_DISCONNECT,
        host_role=MigrationHostRole.DESTINATION_REHEARSAL,
        label=LIFECYCLE_LABELS[StorageLifecycleState.READY_TO_DISCONNECT],
        recommended="Safe disconnect Mercury HDD",
        writes_allowed=False,
        package_status="DESTINATION_PACKAGE_VERIFIED",
    )
    assert dashboard_next_action_short(snap) == "Safely disconnect the Mercury HDD"


def test_prepare_and_restore_round_trip(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
            destination_rehearsal_active=False,
        ),
        path=host_path,
    )
    prepare_disconnect(operator_intent="test")
    assert writes_allowed() is False
    _patch_healthy_mount(monkeypatch)
    result = restore_source_writer(operator_intent="test", require_strong_phrase=False)
    assert result.ok
    assert writes_allowed() is True
