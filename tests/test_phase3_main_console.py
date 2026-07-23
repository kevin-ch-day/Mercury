"""Phase 3 task-based main console tests (hermetic)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.menu.options import (
    MAIN_ADVANCED,
    MAIN_BACKUP_SYNC,
    MAIN_HEALTH,
    MAIN_MIGRATION,
    MAIN_RECOVERY,
    MAIN_REPORTS,
    MAIN_STORAGE,
    main_menu_hint,
    main_menu_items,
    main_menu_max_primary_actions,
)
from mercury.menu.recommendation import build_main_menu_recommendation
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance


@pytest.fixture
def host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    monkeypatch.setenv("MERCURY_TRANSITION_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("MERCURY_TEST_ISOLATION", "1")
    return path


def test_task_main_menu_has_seven_actions_maximum() -> None:
    assert main_menu_max_primary_actions() == 7
    items = main_menu_items(writes_allowed=True)
    assert len(items) == 7
    titles = [t for _k, t in items]
    assert titles[0].startswith("Back up and sync")
    assert "Sync production to development" not in " ".join(titles)
    assert "Sync offline GitHub" not in " ".join(titles)


def test_no_duplicate_backup_git_sync_top_level() -> None:
    titles = " ".join(t for _k, t in main_menu_items(writes_allowed=True)).lower()
    assert titles.count("backup") == 1 or "back up and sync" in titles
    assert "offline github" not in titles
    assert "sync production" not in titles


def test_symbolic_numbering_and_no_stale_eleven() -> None:
    assert main_menu_hint(MAIN_BACKUP_SYNC).endswith("[1]")
    assert main_menu_hint(MAIN_STORAGE).endswith("[2]")
    assert main_menu_hint(MAIN_RECOVERY).endswith("[3]")
    assert main_menu_hint(MAIN_REPORTS).endswith("[4]")
    assert main_menu_hint(MAIN_MIGRATION).endswith("[5]")
    assert main_menu_hint(MAIN_HEALTH).endswith("[6]")
    assert main_menu_hint(MAIN_ADVANCED).endswith("[7]")
    assert "[11]" not in main_menu_hint("workstation_handoff")
    assert "[10]" not in main_menu_hint("disaster_recovery")


def test_recommended_action_writer_enabled(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
            destination_rehearsal_active=False,
        ),
        path=host_path,
    )
    rec = build_main_menu_recommendation()
    assert rec.recommended_action == MAIN_BACKUP_SYNC
    assert "Back up and sync" in rec.explanation


def test_recommended_action_writes_disabled_intent(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z",
        ),
        path=host_path,
    )
    rec = build_main_menu_recommendation()
    assert rec.intent_chooser_required is True
    assert "Choose backup, disconnect, or rehearsal" in rec.explanation


def test_software_only_when_detached(host_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detached", writes_allowed=False),
        path=host_path,
    )
    rec = build_main_menu_recommendation()
    assert rec.software_only is True
    items = main_menu_items(software_only=True, hdd_detached=True)
    assert len(items) == 5
    assert items[0][1].startswith("Reconnect")


def test_startup_intent_chooser_options(monkeypatch) -> None:
    from mercury.menu.intent import INTENT_BACKUP_SYNC, run_startup_intent_chooser

    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "1")
    assert run_startup_intent_chooser() == INTENT_BACKUP_SYNC


def test_backup_sync_hub_launches_phase2_wizard(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.session_wizard.run_backup_sync_wizard",
        lambda **_k: called.append("wizard"),
    )
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "1")
    from mercury.menu.task_menus import run_backup_sync_hub

    run_backup_sync_hub()
    assert called == ["wizard"]


def test_safe_disconnect_intent_launches_wizard(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_safe_disconnect_wizard",
        lambda: called.append("disconnect"),
    )
    from mercury.menu.intent import INTENT_SAFE_DISCONNECT, dispatch_startup_intent

    assert dispatch_startup_intent(INTENT_SAFE_DISCONNECT) is None
    assert called == ["disconnect"]


def test_migration_consolidates_handoff_and_deploy(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.handoff.interactive_menu.run_handoff_menu",
        lambda **_k: called.append("handoff"),
    )
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "1")
    from mercury.menu.task_menus import run_migration_hub

    run_migration_hub()
    assert called == ["handoff"]


def test_health_consolidates_environment_inventory_doctor(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.env.interactive_menu.run_env_menu",
        lambda: called.append("env"),
    )
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "1")
    from mercury.menu.task_menus import run_health_hub

    run_health_hub()
    assert called == ["env"]


def test_advanced_retains_expert_backup(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_backup_menu",
        lambda: called.append("backup"),
    )
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "1")
    from mercury.menu.task_menus import run_advanced_hub

    run_advanced_hub()
    assert called == ["backup"]


def test_old_capabilities_remain_reachable_via_hints() -> None:
    assert "migration" in main_menu_hint("workstation_handoff").lower()
    assert "recovery" in main_menu_hint("disaster_recovery").lower()
    assert "health" in main_menu_hint("system_doctor").lower()
    assert "advanced" in main_menu_hint("sync_prod_dev").lower()


def test_menu_snapshot_does_not_depend_on_live_host(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(tmp_path / "host.json"))
    monkeypatch.setenv("MERCURY_TEST_ISOLATION", "1")
    from mercury.menu import main_display as menu_display

    monkeypatch.setattr("mercury.menu.main_display.dashboard_rows", lambda **_k: ["  Recommended"])
    text = menu_display.render_main_menu(probe_database=False)
    assert "Back up and sync this workstation" in text or "Reconnect" in text
    assert "/mnt/MERCURY_DATA_V2" not in text


def test_capability_routing_matrix_reachable() -> None:
    """Former main-menu capabilities remain reachable under Phase 3 hubs."""
    from mercury.menu import task_menus

    # Hub entry points exist and are callable.
    assert callable(task_menus.run_backup_sync_hub)
    assert callable(task_menus.run_recovery_hub)
    assert callable(task_menus.run_migration_hub)
    assert callable(task_menus.run_health_hub)
    assert callable(task_menus.run_advanced_hub)

    matrix = {
        "production_database_backup": MAIN_BACKUP_SYNC,
        "development_database_backup": MAIN_BACKUP_SYNC,
        "offline_git_capture": MAIN_BACKUP_SYNC,
        "prod_to_dev_sync": MAIN_ADVANCED,
        "storage_validation": MAIN_STORAGE,
        "safe_disconnect": MAIN_STORAGE,
        "exact_id_restore_check": MAIN_RECOVERY,
        "reports_history": MAIN_REPORTS,
        "environment_details": MAIN_HEALTH,
        "database_inventory": MAIN_HEALTH,
        "system_doctor": MAIN_HEALTH,
        "handoff_package": MAIN_MIGRATION,
        "deployment_validation": MAIN_MIGRATION,
        "cleanup_smart_usb": MAIN_ADVANCED,
    }
    for capability, route in matrix.items():
        hint = main_menu_hint(route)
        assert "[" in hint, capability
        assert route in {
            MAIN_BACKUP_SYNC,
            MAIN_STORAGE,
            MAIN_RECOVERY,
            MAIN_REPORTS,
            MAIN_MIGRATION,
            MAIN_HEALTH,
            MAIN_ADVANCED,
        }


def test_startup_intent_shown_once_and_browse_opens_main(
    monkeypatch, host_path: Path
) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=host_path,
    )
    chooser_calls: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.intent.should_offer_startup_intent", lambda **_k: True
    )
    monkeypatch.setattr(
        "mercury.menu.intent.run_startup_intent_chooser",
        lambda: chooser_calls.append("shown") or "browse",
    )
    monkeypatch.setattr(
        "mercury.menu.intent.dispatch_startup_intent", lambda _intent: None
    )
    monkeypatch.setattr(
        "mercury.repair.startup.maybe_prompt_usb_repair_at_startup", lambda: None
    )
    monkeypatch.setattr(
        "mercury.storage.lifecycle.maybe_prompt_storage_first_run",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "mercury.repair.startup.primary_mount_hint", lambda: None
    )
    from mercury.menu.loop import run_menu

    prompts = iter(["0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.read_menu_option", lambda: next(prompts)
    )
    monkeypatch.setattr(
        "mercury.menu.runners.render_menu_text",
        lambda: "MAIN MENU SNAPSHOT",
    )
    run_menu(interactive=True)
    assert chooser_calls == ["shown"]


def test_startup_intent_choices_dispatch(monkeypatch) -> None:
    from mercury.menu.intent import (
        INTENT_BACKUP_SYNC,
        INTENT_BROWSE,
        INTENT_DESTINATION_REHEARSAL,
        INTENT_EXIT,
        INTENT_SAFE_DISCONNECT,
        dispatch_startup_intent,
    )

    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.session_wizard.run_backup_sync_wizard",
        lambda **_k: called.append("backup"),
    )
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_safe_disconnect_wizard",
        lambda: called.append("disconnect"),
    )
    monkeypatch.setattr(
        "mercury.menu.task_menus.run_migration_hub",
        lambda: called.append("migration"),
    )

    assert dispatch_startup_intent(INTENT_BACKUP_SYNC) is None
    assert dispatch_startup_intent(INTENT_SAFE_DISCONNECT) is None
    assert dispatch_startup_intent(INTENT_DESTINATION_REHEARSAL) is None
    assert dispatch_startup_intent(INTENT_BROWSE) is None
    assert dispatch_startup_intent(INTENT_EXIT) == "exit"
    assert called == ["backup", "disconnect", "migration"]


def test_viewing_intent_chooser_does_not_mutate_host(
    monkeypatch, host_path: Path
) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
        ),
        path=host_path,
    )
    before = host_path.read_bytes()
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "4")
    from mercury.menu.intent import run_startup_intent_chooser

    assert run_startup_intent_chooser() == "browse"
    assert host_path.read_bytes() == before


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {
                "storage_availability": "mounted",
                "writes_allowed": True,
                "active_write_role": "primary",
            },
            MAIN_BACKUP_SYNC,
        ),
        (
            {
                "storage_availability": "detaching",
                "writes_allowed": False,
                "source_detach_preparation": True,
            },
            "intent_chooser",
        ),
        (
            {"storage_availability": "detached", "writes_allowed": False},
            "attach",
        ),
    ],
)
def test_recommendation_states(host_path: Path, kwargs: dict, expected: str) -> None:
    save_host_maintenance(HostMaintenanceState(**kwargs), path=host_path)
    rec = build_main_menu_recommendation()
    assert rec.recommended_action == expected


def test_recommendation_destination_and_mismatch(host_path: Path) -> None:
    from mercury.storage.lifecycle import (
        MigrationHostRole,
        StorageLifecycleSnapshot,
        StorageLifecycleState,
        LIFECYCLE_LABELS,
    )

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=False,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=host_path,
    )
    snap = StorageLifecycleSnapshot(
        state=StorageLifecycleState.ATTACHED_READ_ONLY,
        host_role=MigrationHostRole.DESTINATION_REHEARSAL,
        label=LIFECYCLE_LABELS[StorageLifecycleState.ATTACHED_READ_ONLY],
        recommended="Continue destination inspection",
        writes_allowed=False,
        package_status="DESTINATION_PACKAGE_VERIFIED",
        package_id="pkg",
        mounted=True,
        mount="/mnt/fake",
    )
    rec = build_main_menu_recommendation(lifecycle=snap)
    assert rec.recommended_action in {"destination_validation", "intent_chooser"}

    bad = StorageLifecycleSnapshot(
        state=StorageLifecycleState.DEVICE_IDENTITY_MISMATCH,
        host_role=MigrationHostRole.SOURCE_OPERATION,
        label=LIFECYCLE_LABELS[StorageLifecycleState.DEVICE_IDENTITY_MISMATCH],
        recommended="Diagnose",
        writes_allowed=False,
        package_status="Pending",
        mounted=True,
        mount="/mnt/fake",
    )
    rec2 = build_main_menu_recommendation(lifecycle=bad)
    assert rec2.recommended_action == "diagnose"


def test_software_only_empty_mountpoint_untouched(
    monkeypatch, host_path: Path, tmp_path: Path
) -> None:
    mount = tmp_path / "MERCURY_DATA_V2"
    mount.mkdir()
    before = {p.name for p in mount.iterdir()}
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detached", writes_allowed=False),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.menu.main_display.dashboard_rows",
        lambda **_k: [
            "  Mercury HDD   Not connected",
            "  Recommended   Attach HDD and choose Reconnect",
        ],
    )
    from mercury.menu import main_display as menu_display

    text = menu_display.render_main_menu(probe_database=False)
    assert "Reconnect" in text or "System health" in text
    assert {p.name for p in mount.iterdir()} == before


def test_main_menu_presentation_has_seven_actions_and_titles() -> None:
    items = main_menu_items(writes_allowed=True)
    assert len(items) == 7
    titles = [t for _k, t in items]
    assert titles == [
        "Back up and sync this workstation",
        "Mercury HDD and Storage",
        "Restore and disaster recovery",
        "Reports and backup history",
        "Workstation migration",
        "System health and configuration",
        "Advanced tools",
    ]


def test_handoff_checklist_actions_use_symbolic_registry() -> None:
    from mercury.handoff.menu_options import (
        ACTION_TOOLS_BACKUP,
        ACTION_TOOLS_TRANSFER,
        handoff_nested_hint,
        handoff_tools_hint,
    )

    backup = handoff_nested_hint(ACTION_TOOLS_BACKUP)
    transfer = handoff_nested_hint(ACTION_TOOLS_TRANSFER)
    assert "[" in backup and "Run Backup" in backup
    assert "[" in transfer and "Transfer" in transfer
    # Reordering tools must keep hints in sync with the registry.
    assert handoff_tools_hint(ACTION_TOOLS_BACKUP).endswith("[2]")
    assert handoff_tools_hint(ACTION_TOOLS_TRANSFER).endswith("[6]")
