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
    assert rec.recommended_action == "safe_disconnect"
    assert "Safely disconnect" in rec.explanation


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


def test_startup_intent_recommends_safe_disconnect(host_path: Path, monkeypatch) -> None:
    from mercury.menu.intent import (
        INTENT_BACKUP_SYNC,
        INTENT_SAFE_DISCONNECT,
        build_startup_intent_options,
        recommended_startup_action,
        run_startup_intent_chooser,
    )

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="destination_rehearsal_final_source_20260723T161213Z",
        ),
        path=host_path,
    )
    assert recommended_startup_action() == INTENT_SAFE_DISCONNECT
    options = build_startup_intent_options()
    assert options[0][2] == INTENT_SAFE_DISCONNECT
    assert "recommended" in options[0][1]
    assert options[1][2] == INTENT_BACKUP_SYNC
    assert "again" in options[1][1].lower()

    printed: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.intent.output.write", lambda msg="": printed.append(str(msg))
    )
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "1")
    assert run_startup_intent_chooser() == INTENT_SAFE_DISCONNECT
    text = "\n".join(printed)
    assert "CURRENT SESSION" in text
    assert "Safely disconnect the Mercury HDD" in text
    assert "Recommended" in text


def test_startup_invalid_choice_reprompts(monkeypatch, host_path: Path) -> None:
    from mercury.menu.intent import INTENT_SAFE_DISCONNECT, run_startup_intent_chooser

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    answers = iter(["9", "1"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask", lambda *_a, **_k: next(answers)
    )
    printed: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.intent.output.write", lambda msg="": printed.append(str(msg))
    )
    assert run_startup_intent_chooser() == INTENT_SAFE_DISCONNECT
    assert any("Invalid" in line or "invalid" in line.lower() for line in printed)

    from mercury.menu.intent import INTENT_SAFE_DISCONNECT, run_startup_intent_chooser

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "1")
    assert run_startup_intent_chooser() == INTENT_SAFE_DISCONNECT


def test_backup_sync_hub_launches_phase2_wizard(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.session_wizard.run_backup_sync_wizard",
        lambda **_k: called.append("wizard"),
    )
    answers = iter(["1", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    from mercury.menu.task_menus import run_backup_sync_hub

    run_backup_sync_hub()
    assert called == ["wizard"]


def test_backup_sync_hub_routes_production_and_development(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_production_backup_flow",
        lambda: called.append("prod"),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_development_backup_flow",
        lambda: called.append("dev"),
    )
    from mercury.menu.task_menus import run_backup_sync_hub

    answers = iter(["2", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    run_backup_sync_hub()
    answers = iter(["3", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    run_backup_sync_hub()
    assert called == ["prod", "dev"]


def test_backup_sync_hub_title_again_when_package_verified(
    monkeypatch, host_path: Path
) -> None:
    from mercury.menu import task_menus
    from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    titles: list[str] = []
    monkeypatch.setattr(
        task_menus.display_screen,
        "open_screen",
        lambda title: titles.append(title),
    )
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "0")
    task_menus.run_backup_sync_hub()
    assert titles and titles[0] == "Back up and sync again"


def test_safe_disconnect_intent_launches_wizard(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_safe_disconnect_wizard",
        lambda: called.append("disconnect") or True,
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
    answers = iter(["1", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    from mercury.menu.task_menus import run_migration_hub

    run_migration_hub()
    assert called == ["handoff"]


def test_health_consolidates_environment_inventory_doctor(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.env.interactive_menu.run_env_menu",
        lambda: called.append("env"),
    )
    answers = iter(["1", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    from mercury.menu.task_menus import run_health_hub

    run_health_hub()
    assert called == ["env"]


def test_advanced_retains_expert_backup(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_backup_menu",
        lambda: called.append("backup"),
    )
    answers = iter(["1", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
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
        OUTCOME_CANCELLED,
        OUTCOME_CONTINUE,
        OUTCOME_EXIT,
        dispatch_startup_intent,
    )

    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.session_wizard.run_backup_sync_wizard",
        lambda **_k: called.append("backup") or object(),
    )
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_safe_disconnect_wizard",
        lambda: called.append("disconnect") or True,
    )
    monkeypatch.setattr(
        "mercury.menu.task_menus.run_destination_rehearsal_hub",
        lambda: called.append("rehearsal"),
    )

    assert dispatch_startup_intent(INTENT_BACKUP_SYNC) is OUTCOME_CONTINUE
    assert dispatch_startup_intent(INTENT_SAFE_DISCONNECT) is OUTCOME_CONTINUE
    assert dispatch_startup_intent(INTENT_DESTINATION_REHEARSAL) is OUTCOME_CONTINUE
    assert dispatch_startup_intent(INTENT_BROWSE) is OUTCOME_CONTINUE
    assert dispatch_startup_intent(INTENT_EXIT) == OUTCOME_EXIT
    assert called == ["backup", "disconnect", "rehearsal"]


def test_safe_disconnect_cancel_returns_to_intent(monkeypatch) -> None:
    from mercury.menu.intent import (
        INTENT_SAFE_DISCONNECT,
        OUTCOME_CANCELLED,
        dispatch_startup_intent,
    )

    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_safe_disconnect_wizard",
        lambda: False,
    )
    assert dispatch_startup_intent(INTENT_SAFE_DISCONNECT) == OUTCOME_CANCELLED

    from mercury.menu.intent import (
        INTENT_BACKUP_SYNC,
        OUTCOME_CANCELLED,
        dispatch_startup_intent,
    )

    monkeypatch.setattr(
        "mercury.backup.session_wizard.run_backup_sync_wizard",
        lambda **_k: None,
    )
    assert dispatch_startup_intent(INTENT_BACKUP_SYNC) == OUTCOME_CANCELLED


def test_startup_option_order_by_state(host_path: Path) -> None:
    from mercury.menu.intent import (
        INTENT_BACKUP_SYNC,
        INTENT_RECONNECT,
        INTENT_SAFE_DISCONNECT,
        INTENT_VERIFY_PACKAGE,
        build_startup_intent_options,
        recommended_startup_action,
    )

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    opts = build_startup_intent_options()
    assert [a for _k, _l, a in opts][0] == INTENT_SAFE_DISCONNECT
    assert recommended_startup_action() == INTENT_SAFE_DISCONNECT

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
        ),
        path=host_path,
    )
    # Writes enabled: startup intent is not offered, but builder still prefers backup.
    assert recommended_startup_action() == INTENT_BACKUP_SYNC

    save_host_maintenance(
        HostMaintenanceState(storage_availability="detached", writes_allowed=False),
        path=host_path,
    )
    opts = build_startup_intent_options()
    assert opts[0][2] == INTENT_RECONNECT

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="Pending",
        ),
        path=host_path,
    )
    opts = build_startup_intent_options()
    assert opts[0][2] == INTENT_VERIFY_PACKAGE


def test_viewing_intent_chooser_does_not_mutate_host(
    monkeypatch, host_path: Path
) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    before = host_path.read_bytes()
    # [4] Browse when disconnect is recommended first.
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "4")
    from mercury.menu.intent import INTENT_BROWSE, run_startup_intent_chooser

    assert run_startup_intent_chooser() == INTENT_BROWSE
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
            "verify_package",
        ),
        (
            {
                "storage_availability": "detaching",
                "writes_allowed": False,
                "source_detach_preparation": True,
                "package_verification_status": "DESTINATION_PACKAGE_VERIFIED",
                "package_id": "pkg",
            },
            "safe_disconnect",
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
        recommended="Continue destination validation",
        writes_allowed=False,
        package_status="DESTINATION_PACKAGE_VERIFIED",
        package_id="pkg",
        mounted=True,
        mount="/mnt/fake",
    )
    rec = build_main_menu_recommendation(lifecycle=snap)
    assert rec.recommended_action == "destination_validation"

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


def test_main_menu_marks_storage_recommended_when_safe_disconnect(
    host_path: Path,
) -> None:
    from mercury.menu.options import MAIN_STORAGE, main_menu_items
    from mercury.menu.recommendation import (
        build_main_menu_recommendation,
        main_menu_action_for_recommendation,
    )

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    rec = build_main_menu_recommendation()
    assert rec.recommended_action == "safe_disconnect"
    assert main_menu_action_for_recommendation(rec.recommended_action) == MAIN_STORAGE
    items = dict(
        main_menu_items(
            writes_allowed=False,
            recommended_action_id=MAIN_STORAGE,
        )
    )
    assert "recommended" in items["2"]
    assert "recommended" not in items["1"]


def test_should_offer_startup_intent_follows_recommendation(host_path: Path) -> None:
    from mercury.menu.intent import should_offer_startup_intent

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="pkg",
        ),
        path=host_path,
    )
    assert should_offer_startup_intent() is True

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
        ),
        path=host_path,
    )
    assert should_offer_startup_intent() is False


def test_destination_rehearsal_hub_offers_read_paths(monkeypatch, host_path: Path) -> None:
    from mercury.menu import task_menus

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="destination_rehearsal_final_source_20260723T161213Z",
        ),
        path=host_path,
    )
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_storage_menu",
        lambda: called.append("storage"),
    )
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_safe_disconnect_wizard",
        lambda: called.append("disconnect"),
    )
    # [1]=safe disconnect, [2]=review package
    answers = iter(["2", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(task_menus.display_screen, "write_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(task_menus.display_screen, "open_screen", lambda *_a, **_k: None)
    monkeypatch.setattr(task_menus.output, "write", lambda *_a, **_k: None)
    task_menus.run_destination_rehearsal_hub()
    assert called == ["storage"]


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
