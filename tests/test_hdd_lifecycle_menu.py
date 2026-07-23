"""Mercury HDD lifecycle menu + dashboard (mocked; no real detach)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.menu.actions import menu_action_blocked_for_writes, menu_actions, resolve_menu_action
from mercury.menu.options import (
    ACTION_BACKUP,
    ACTION_HDD_STORAGE,
    ACTION_SYNC,
    WRITES_DISABLED_SUFFIX,
    main_menu_hint,
    main_menu_items,
)
from mercury.storage.hdd_menu_options import (
    STORAGE_CHANGE_MODE,
    STORAGE_MAINTENANCE,
    STORAGE_RECOMMENDED_ACTION,
    STORAGE_STATUS_VALIDATE,
    change_mode_options,
    dashboard_hdd_status_line,
    dashboard_next_action_short,
    hdd_menu_hint,
    hdd_menu_header_state,
    hdd_menu_option_by_action,
    hdd_menu_render_options,
    primary_action_count,
    recommended_primary_label,
)
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance
from mercury.storage.lifecycle import (
    LIFECYCLE_LABELS,
    StorageLifecycleSnapshot,
    StorageLifecycleState,
    MigrationHostRole,
    assess_storage_lifecycle,
    ensure_no_mountpoint_mkdir,
    recommended_next_action,
    writes_disabled_redirect_message,
)


@pytest.fixture
def host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    monkeypatch.setenv(
        "MERCURY_TRANSITION_LEDGER_PATH",
        str(tmp_path / "transition_ledger.jsonl"),
    )
    return path


def _snap(
    state: StorageLifecycleState,
    *,
    writes_allowed: bool = False,
    package_verified: bool = False,
    host_role: MigrationHostRole | None = None,
) -> StorageLifecycleSnapshot:
    role = host_role
    if role is None:
        role = (
            MigrationHostRole.DESTINATION_REHEARSAL
            if not writes_allowed
            else MigrationHostRole.SOURCE_OPERATION
        )
    return StorageLifecycleSnapshot(
        state=state,
        host_role=role,
        label=LIFECYCLE_LABELS[state],
        recommended=recommended_next_action(
            state, package_verified=package_verified, role=role
        ),
        writes_allowed=writes_allowed,
        active_write_role="primary" if writes_allowed else "none",
        package_status="DESTINATION_PACKAGE_VERIFIED" if package_verified else "",
        package_id="phase3b_test" if package_verified else "",
        device_label="MERCURY_DATA_V2",
        device_model="WDC WD10JDRW-11CFYS0",
        device_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        filesystem="ext4",
        mount="/mnt/MERCURY_DATA_V2"
        if state
        not in {
            StorageLifecycleState.DETACHED,
            StorageLifecycleState.DEVICE_NOT_FOUND,
        }
        else "",
        mounted=state
        not in {
            StorageLifecycleState.DETACHED,
            StorageLifecycleState.DEVICE_NOT_FOUND,
        },
    )


def _option_labels(snapshot: StorageLifecycleSnapshot) -> list[str]:
    return [label for _key, label in hdd_menu_render_options(snapshot)]


def test_main_menu_mercury_hdd_is_option_one() -> None:
    items = main_menu_items(writes_allowed=True)
    assert items[0] == ("1", "Mercury HDD and Storage")
    assert main_menu_hint(ACTION_HDD_STORAGE) == "Mercury HDD and Storage [1]"
    action = resolve_menu_action("1")
    assert action is not None
    assert action.action_id == ACTION_HDD_STORAGE


def test_main_menu_actions_shift_and_symbolic_hints_stay_synced() -> None:
    acts = menu_actions()
    assert acts["2"].action_id == ACTION_BACKUP
    assert acts["3"].action_id == ACTION_SYNC
    assert "Workstation handoff [11]" == main_menu_hint("workstation_handoff")
    assert "[10]" not in main_menu_hint("workstation_handoff")


def test_main_menu_marks_write_actions_unavailable_when_writes_disabled() -> None:
    items = dict(main_menu_items(writes_allowed=False))
    assert WRITES_DISABLED_SUFFIX in items["2"]
    assert WRITES_DISABLED_SUFFIX in items["3"]
    assert WRITES_DISABLED_SUFFIX not in items["1"]
    assert WRITES_DISABLED_SUFFIX not in items["4"]


def test_write_action_blocked_redirects_to_storage_menu(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
        ),
        path=host_path,
    )
    action = resolve_menu_action("2")
    assert action is not None
    assert menu_action_blocked_for_writes(action) is True
    msg = writes_disabled_redirect_message()
    assert "Operation unavailable" in msg
    assert main_menu_hint(ACTION_HDD_STORAGE) in msg
    assert "Reconnect or change storage mode" in msg
    assert "per-database" not in msg.lower()


@pytest.mark.parametrize(
    ("state", "fragment"),
    [
        (StorageLifecycleState.ATTACHED_WRITER_ENABLED, "backups enabled"),
        (StorageLifecycleState.ATTACHED_WRITER_DISABLED, "writes disabled"),
        (StorageLifecycleState.READY_TO_DISCONNECT, "safe disconnect ready"),
        (StorageLifecycleState.DETACHED, "not connected"),
        (StorageLifecycleState.ATTACHED_READ_ONLY, "read-only"),
        (StorageLifecycleState.DEVICE_IDENTITY_MISMATCH, "Wrong or unrecognized"),
    ],
)
def test_lifecycle_labels(state: StorageLifecycleState, fragment: str) -> None:
    assert fragment.lower() in LIFECYCLE_LABELS[state].lower()


def test_recommended_action_ready_to_disconnect() -> None:
    assert (
        recommended_next_action(
            StorageLifecycleState.READY_TO_DISCONNECT, package_verified=True
        )
        == "Safe disconnect Mercury HDD"
    )


def test_recommended_action_detached() -> None:
    text = recommended_next_action(StorageLifecycleState.DETACHED)
    assert "Attach" in text and "Reconnect" in text


@pytest.mark.parametrize(
    ("state", "writes", "pkg", "needle", "blocked"),
    [
        (StorageLifecycleState.ATTACHED_WRITER_ENABLED, True, False, "Prepare HDD for safe disconnect", False),
        (StorageLifecycleState.READY_TO_DISCONNECT, False, True, "Safe disconnect Mercury HDD", False),
        (StorageLifecycleState.ATTACHED_WRITER_DISABLED, False, True, "Safe disconnect Mercury HDD", False),
        (StorageLifecycleState.PREPARING_TO_DISCONNECT, False, True, "Recheck disconnect blockers", False),
        (StorageLifecycleState.PREPARING_TO_DISCONNECT, False, True, "Recheck disconnect blockers", True),
        (StorageLifecycleState.DETACHED, False, True, "Reconnect or inspect", False),
        (StorageLifecycleState.ATTACHED_READ_ONLY, False, True, "destination inspection", False),
        (StorageLifecycleState.DEVICE_IDENTITY_MISMATCH, False, False, "Diagnose", False),
        (StorageLifecycleState.ATTACHED_WRITER_DISABLED, False, False, "Verify destination package", False),
    ],
)
def test_recommended_primary_label_by_state(
    state: StorageLifecycleState,
    writes: bool,
    pkg: bool,
    needle: str,
    blocked: bool,
) -> None:
    snap = _snap(state, writes_allowed=writes, package_verified=pkg)
    if blocked:
        snap = StorageLifecycleSnapshot(
            **{**snap.__dict__, "disconnect_blocked": True, "notes": ("open handle via fuser",)}
        )
    label, _suffix = recommended_primary_label(snap)
    assert needle.lower() in label.lower()


def test_hdd_menu_always_four_primary_actions() -> None:
    for state in StorageLifecycleState:
        snap = _snap(state, package_verified=True)
        opts = hdd_menu_render_options(snap)
        assert len(opts) == 4
        assert primary_action_count(snap) == 4
        assert opts[0][0] == "1"
        assert opts[1][1] == "Storage status and validation"
        assert opts[2][1] == "Reconnect or change storage mode"
        assert opts[3][1] == "Cleanup and advanced tools"
        joined = "\n".join(_option_labels(snap)).lower()
        assert "enable mercury writes" not in joined
        assert "destination read-only inspection" not in joined
        assert "advanced storage operations" not in joined


def test_hdd_menu_safe_disconnect_ready_is_option_one() -> None:
    options = hdd_menu_render_options(
        _snap(StorageLifecycleState.READY_TO_DISCONNECT, package_verified=True)
    )
    assert options[0][0] == "1"
    assert "Safe disconnect Mercury HDD" in options[0][1]
    assert "ready" in options[0][1]


def test_hdd_menu_hides_enable_writes_in_change_mode_during_detach_prep() -> None:
    opts = change_mode_options(_snap(StorageLifecycleState.PREPARING_TO_DISCONNECT))
    labels = " ".join(label for _k, label, _a in opts).lower()
    assert "enable" not in labels or "restore" in labels
    assert "keep writes disabled" in labels
    assert "already disabled" not in labels


def test_change_mode_detached_shows_reconnect_only() -> None:
    opts = change_mode_options(_snap(StorageLifecycleState.DETACHED, package_verified=True))
    labels = [label for _k, label, _a in opts]
    assert any("read-only" in x.lower() for x in labels)
    assert any("source" in x.lower() for x in labels)
    assert any("destination rehearsal" in x.lower() for x in labels)
    assert len(opts) == 3


def test_change_mode_writer_enabled_hides_enable() -> None:
    opts = change_mode_options(
        _snap(StorageLifecycleState.ATTACHED_WRITER_ENABLED, writes_allowed=True)
    )
    labels = " ".join(label for _k, label, _a in opts).lower()
    assert "disable writes and prepare disconnect" in labels
    assert "enable source" not in labels
    assert len(opts) == 2


def test_symbolic_primary_hints() -> None:
    assert hdd_menu_hint(STORAGE_STATUS_VALIDATE) == "Storage status and validation [2]"
    assert hdd_menu_hint(STORAGE_CHANGE_MODE) == "Reconnect or change storage mode [3]"
    assert hdd_menu_hint(STORAGE_MAINTENANCE) == "Cleanup and advanced tools [4]"
    key, _ = hdd_menu_option_by_action(STORAGE_RECOMMENDED_ACTION)
    assert key == "1"


def test_header_state_avoids_safe_disconnect_ready_duplication() -> None:
    snap = _snap(StorageLifecycleState.READY_TO_DISCONNECT, package_verified=True)
    assert "writes disabled" in hdd_menu_header_state(snap).lower()
    assert "safe disconnect ready" not in hdd_menu_header_state(snap).lower()


def test_dashboard_next_action_short_for_ready() -> None:
    snap = _snap(StorageLifecycleState.READY_TO_DISCONNECT, package_verified=True)
    assert dashboard_next_action_short(snap) == "Back up, disconnect, or continue rehearsal"
    assert "writes disabled" in dashboard_hdd_status_line(snap).lower()


def test_assess_lifecycle_detached(host_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detached", writes_allowed=False),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: SimpleNamespace(identity=None, errors=["not found"]),
    )
    snap = assess_storage_lifecycle(probe_disconnect=False)
    assert snap.state == StorageLifecycleState.DETACHED
    assert "not connected" in snap.label.lower()


def test_assess_lifecycle_writes_disabled_ready(
    host_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="phase3b_20260722T193251Z",
            destination_rehearsal_in_progress=True,
        ),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: SimpleNamespace(
            identity=SimpleNamespace(
                label="MERCURY_DATA_V2",
                model="WDC WD10JDRW",
                uuid="715f29a9-2671-477b-8c8d-515d190addb9",
                fstype="ext4",
                mountpoint="/mnt/MERCURY_DATA_V2",
                partition_device="/dev/mock",
                parent_device="/dev/mock-parent",
            ),
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "mercury.storage.lifecycle._safe_disconnect_ready",
        lambda **kwargs: True,
    )
    snap = assess_storage_lifecycle(probe_disconnect=True)
    assert snap.state == StorageLifecycleState.READY_TO_DISCONNECT
    assert snap.recommended == "Safe disconnect Mercury HDD"


def test_storage_menu_launches_from_main_menu_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_storage_menu",
        lambda **kwargs: called.append("storage"),
    )
    action = resolve_menu_action("1")
    assert action is not None
    action.runner()
    assert called == ["storage"]


def test_restore_writes_requires_exact_phrase(host_path: Path) -> None:
    from mercury.storage.reconnect import restore_writes_after_reconnect

    save_host_maintenance(
        HostMaintenanceState(writes_allowed=False, active_write_role="none"),
        path=host_path,
    )
    assert restore_writes_after_reconnect(confirm="wrong", path=host_path) is None
    restored = restore_writes_after_reconnect(
        confirm="RESTORE MERCURY WRITES", path=host_path
    )
    assert restored is not None
    assert restored.writes_allowed is True
    assert restored.active_write_role == "primary"


def test_hdd_absent_does_not_create_mountpoint(tmp_path: Path) -> None:
    missing = tmp_path / "MERCURY_DATA_V2"
    with pytest.raises(OSError, match="Refusing to create"):
        ensure_no_mountpoint_mkdir(missing)
    assert not missing.exists()


def test_dashboard_ready_to_disconnect_wording(
    host_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.menu.dashboard import _migration_dashboard_rows
    from mercury.migration.models import (
        MigrationCheck,
        MigrationCheckState,
        MigrationReadinessReport,
    )

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="phase3b_20260722T193251Z",
            destination_rehearsal_in_progress=True,
        ),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.lifecycle.assess_storage_lifecycle",
        lambda **kwargs: _snap(
            StorageLifecycleState.READY_TO_DISCONNECT, package_verified=True
        ),
    )
    report = MigrationReadinessReport(
        policy_state="verified",
        observed_mirror="verified",
        operator_phase="destination validation pending",
        checks=(
            MigrationCheck("active_writer", "Active writer", MigrationCheckState.PASS, "PASS", "none"),
        ),
    )
    rows = "\n".join(_migration_dashboard_rows(report, policy=SimpleNamespace()))
    assert "Mercury HDD" in rows
    assert "Next action" in rows
    assert "Back up, disconnect, or continue rehearsal" in rows
    assert "Detaching · writes off" not in rows
    assert "detach mode" not in rows.lower()


@pytest.mark.parametrize(
    ("name", "state", "writes", "pkg", "role"),
    [
        ("writer_enabled", StorageLifecycleState.ATTACHED_WRITER_ENABLED, True, False, MigrationHostRole.SOURCE_OPERATION),
        ("writes_disabled", StorageLifecycleState.ATTACHED_WRITER_DISABLED, False, False, MigrationHostRole.SOURCE_OPERATION),
        ("ready_disconnect", StorageLifecycleState.READY_TO_DISCONNECT, False, True, MigrationHostRole.DESTINATION_REHEARSAL),
        ("preparing", StorageLifecycleState.PREPARING_TO_DISCONNECT, False, True, MigrationHostRole.DESTINATION_REHEARSAL),
        ("detached", StorageLifecycleState.DETACHED, False, True, MigrationHostRole.SOURCE_OPERATION),
        ("read_only", StorageLifecycleState.ATTACHED_READ_ONLY, False, True, MigrationHostRole.DESTINATION_REHEARSAL),
        ("destination_rehearsal", StorageLifecycleState.ATTACHED_WRITER_DISABLED, False, True, MigrationHostRole.DESTINATION_REHEARSAL),
        ("identity_mismatch", StorageLifecycleState.DEVICE_IDENTITY_MISMATCH, False, False, MigrationHostRole.SOURCE_OPERATION),
        ("package_not_verified", StorageLifecycleState.ATTACHED_WRITER_DISABLED, False, False, MigrationHostRole.SOURCE_OPERATION),
    ],
)
def test_hdd_menu_snapshots_by_state(
    name: str,
    state: StorageLifecycleState,
    writes: bool,
    pkg: bool,
    role: MigrationHostRole,
) -> None:
    snap = _snap(state, writes_allowed=writes, package_verified=pkg, host_role=role)
    opts = hdd_menu_render_options(snap)
    assert len(opts) == 4, name
    assert opts[0][0] == "1", name
    text = "\n".join(f"[{k}] {label}" for k, label in opts)
    assert "[5]" not in text
    # Prefer hide over littering unavailable suffixes on primary menu.
    assert "already disabled" not in text
    assert "already enabled" not in text
    assert "unavailable ·" not in text


def test_cleanup_advanced_options_present() -> None:
    from mercury.storage.hdd_menu_options import cleanup_advanced_options

    opts = cleanup_advanced_options(cleanup_locked=True)
    assert len(opts) == 6
    labels = " ".join(label for _k, label, _a in opts).lower()
    assert "cleanup status" in labels
    assert "smart" in labels
    assert "troubleshooting" in labels


def test_menu_snapshot_writes_disabled_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.menu import main_display as menu_display

    monkeypatch.setattr("mercury.menu.main_display.dashboard_rows", lambda **kwargs: [])
    monkeypatch.setattr("mercury.menu.main_display.hdd_writes_allowed", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "mercury.storage.host_maintenance.load_host_maintenance",
        lambda: SimpleNamespace(
            storage_availability="detaching",
            destination_rehearsal_in_progress=True,
        ),
    )
    text = menu_display.render_main_menu(probe_database=False)
    assert "Mercury HDD and Storage" in text
    assert "Backup source databases" in text
    assert WRITES_DISABLED_SUFFIX in text
    assert "[11] Workstation handoff" in text


def test_menu_snapshot_detached_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.menu import main_display as menu_display
    from mercury.menu.options import HDD_ABSENT_SUFFIX, REPORTS_LIMITED_SUFFIX

    monkeypatch.setattr("mercury.menu.main_display.dashboard_rows", lambda **kwargs: [])
    monkeypatch.setattr("mercury.menu.main_display.hdd_writes_allowed", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "mercury.storage.host_maintenance.load_host_maintenance",
        lambda: SimpleNamespace(
            storage_availability="detached",
            destination_rehearsal_in_progress=False,
        ),
    )
    text = menu_display.render_main_menu(probe_database=False)
    assert HDD_ABSENT_SUFFIX in text
    assert REPORTS_LIMITED_SUFFIX in text


def test_software_only_first_run_prompt_text() -> None:
    from mercury.storage.lifecycle import render_storage_first_run_prompt

    text = render_storage_first_run_prompt()
    assert "has not been configured" in text
    assert "software-only" in text
    assert "Initialize a new Mercury HDD" in text


def test_software_only_non_tty_skips_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.storage.lifecycle import maybe_prompt_storage_first_run

    monkeypatch.setattr(
        "mercury.storage.lifecycle.software_only_startup_needed", lambda **kwargs: True
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert maybe_prompt_storage_first_run(interactive=True) == "software_only"


def test_legacy_usb_never_selected_as_mercury_hdd() -> None:
    from mercury.core.storage_roles import DEFAULT_PRIMARY_UUID
    from mercury.storage.block_device import resolve_mercury_block_device

    resolved = resolve_mercury_block_device(require_mounted=False)
    if resolved.identity is None:
        return
    assert resolved.identity.uuid == DEFAULT_PRIMARY_UUID
    assert "USB" not in (resolved.identity.label or "").upper()
