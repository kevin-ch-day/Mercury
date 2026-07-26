"""Nine-area main console routing tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mercury.menu.options import (
    MAIN_ADVANCED,
    MAIN_BACKUP,
    MAIN_DEPLOY,
    MAIN_HEALTH,
    MAIN_MIGRATION,
    MAIN_RECOVERY,
    MAIN_REPO,
    MAIN_REPORTS,
    MAIN_STORAGE,
    MAIN_SYNC,
    main_menu_hint,
    main_menu_items,
    main_menu_max_primary_actions,
    main_menu_option_by_action,
)


EXPECTED_TITLES = [
    "Backup and verification",
    "Database sync and data movement",
    "Git and repository recovery",
    "Mercury HDD and storage",
    "Restore and disaster recovery",
    "Workstation migration",
    "Deployment and handoff",
    "Reports, evidence, and history",
    "System health and configuration",
]


def test_nine_area_main_menu_entries() -> None:
    assert main_menu_max_primary_actions() == 9
    items = main_menu_items(writes_allowed=True)
    assert [k for k, _ in items] == [str(i) for i in range(1, 10)]
    titles = [t for _k, t in items]
    assert titles == EXPECTED_TITLES
    assert "Advanced tools" not in " ".join(titles)


def test_legacy_aliases_map_to_new_homes() -> None:
    assert main_menu_option_by_action(MAIN_ADVANCED)[0] == "1"
    assert main_menu_hint("sync_prod_dev").endswith("[2]")
    assert main_menu_hint("offline_repos").endswith("[3]")
    assert main_menu_hint(MAIN_STORAGE).endswith("[4]")
    assert main_menu_hint(MAIN_RECOVERY).endswith("[5]")
    assert main_menu_hint(MAIN_MIGRATION).endswith("[6]")
    assert main_menu_hint("workstation_handoff").endswith("[7]")
    assert main_menu_hint("system_deployment").endswith("[7]")
    assert main_menu_hint(MAIN_REPORTS).endswith("[8]")
    assert main_menu_hint(MAIN_HEALTH).endswith("[9]")
    assert main_menu_hint(MAIN_BACKUP).endswith("[1]")


def test_all_nine_hubs_reachable_and_non_destructive(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def mark(name: str):
        def _inner(*_a, **_k):
            called.append(name)

        return _inner

    monkeypatch.setattr("mercury.backup.session_wizard.run_backup_sync_wizard", mark("guided"))
    monkeypatch.setattr("mercury.backup.interactive_menu.run_backup_menu", mark("backup_ops"))
    monkeypatch.setattr("mercury.verify.interactive_menu.run_verify_menu", mark("verify"))
    monkeypatch.setattr(
        "mercury.menu.task_menus._show_full_backup_receipts", mark("receipts")
    )
    monkeypatch.setattr("mercury.sync.interactive_menu.run_sync_menu", mark("sync"))
    monkeypatch.setattr(
        "mercury.transfer.build_transfer_bundle", lambda **_k: object()
    )
    monkeypatch.setattr(
        "mercury.transfer.print_transfer_bundle", mark("transfer_status")
    )
    monkeypatch.setattr(
        "mercury.handoff.history.build_handoff_history", lambda **_k: object()
    )
    monkeypatch.setattr(
        "mercury.handoff.terminal.print_handoff_history", mark("transfer_history")
    )
    monkeypatch.setattr(
        "mercury.repo.interactive_menu.run_offline_sync_now", mark("offline_sync")
    )
    monkeypatch.setattr(
        "mercury.repo.interactive_menu.show_offline_sync_receipt", mark("offline_receipt")
    )
    monkeypatch.setattr(
        "mercury.repo.interactive_menu.offline_clone_plan",
        lambda: type("Plan", (), {"root": "/tmp", "entries": [], "receipt_path": None})(),
    )
    monkeypatch.setattr(
        "mercury.repo.offline_terminal.print_offline_clone_plan",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "mercury.repo.inspect_repositories", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        "mercury.repo.load_repo_definitions", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        "mercury.repo.terminal.print_repo_statuses", mark("repo_status")
    )
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_storage_menu", mark("storage")
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_menu.run_restore_menu", mark("restore")
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.run_recovery_dashboard",
        mark("dashboard"),
    )
    monkeypatch.setattr(
        "mercury.recovery.interactive_menu.run_recovery_menu", mark("recovery")
    )
    monkeypatch.setattr(
        "mercury.migration.erebus_capture.menu.run_erebus_source_capture_menu",
        mark("erebus"),
    )
    monkeypatch.setattr(
        "mercury.menu.task_menus.run_destination_rehearsal_hub", mark("destination")
    )
    monkeypatch.setattr(
        "mercury.migration.readiness.build_migration_readiness",
        lambda **_k: object(),
    )
    monkeypatch.setattr(
        "mercury.migration.terminal.print_migration_blockers", mark("mig_blockers")
    )
    monkeypatch.setattr(
        "mercury.migration.terminal.print_migration_next", mark("mig_next")
    )
    monkeypatch.setattr(
        "mercury.deploy.interactive_menu.run_deploy_menu", mark("deploy")
    )
    monkeypatch.setattr(
        "mercury.handoff.interactive_menu.run_handoff_menu", mark("handoff")
    )
    monkeypatch.setattr(
        "mercury.handoff.receiver.build_receiver_handoff_guide", lambda: object()
    )
    monkeypatch.setattr(
        "mercury.handoff.terminal.print_receiver_handoff_guide", mark("receiver")
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_write_database_bundle", mark("bundle")
    )
    monkeypatch.setattr(
        "mercury.handoff.interactive_menu.run_advanced_handoff_tools",
        mark("packaging"),
    )
    monkeypatch.setattr(
        "mercury.menu.task_menus._show_repo_bundle_plan", mark("repo_plan")
    )
    monkeypatch.setattr(
        "mercury.env.interactive_menu.run_env_menu", mark("env")
    )
    monkeypatch.setattr(
        "mercury.database.discovery_menu.run_discover_menu", mark("inventory")
    )
    monkeypatch.setattr(
        "mercury.env.interactive_menu.run_doctor_menu", mark("doctor")
    )
    monkeypatch.setattr(
        "mercury.storage.report.build_storage_status_report",
        lambda: object(),
    )
    monkeypatch.setattr(
        "mercury.storage.terminal.print_storage_status", mark("storage_status")
    )
    monkeypatch.setattr(
        "mercury.menu.options_menu.run_appearance_menu", mark("appearance")
    )
    monkeypatch.setattr(
        "mercury.reporting.interactive_menu.run_reports_menu", mark("reports")
    )

    from mercury.menu import task_menus
    from mercury.menu.runners import run_reports_and_history, run_storage_menu

    assert not hasattr(task_menus, "run_advanced_hub")

    # Enter each hub and immediately Back — no capability runs.
    for runner in (
        task_menus.run_backup_hub,
        task_menus.run_sync_hub,
        task_menus.run_repo_hub,
        task_menus.run_recovery_hub,
        task_menus.run_migration_hub,
        task_menus.run_deploy_handoff_hub,
        task_menus.run_health_hub,
    ):
        monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "0")
        runner()
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "0")
    # storage + reports are direct runners
    run_storage_menu()
    run_reports_and_history()
    # storage/reports were mocked to mark — entering them does call
    assert "storage" in called
    assert "reports" in called
    # Back-only hub entries must not have launched expert menus above.
    for name in ("guided", "sync", "deploy", "handoff", "restore"):
        assert called.count(name) == 0
    # Main [5] opens consolidated dashboard (mocked) once during back-only pass.
    assert called.count("dashboard") == 1

    called.clear()
    # Main [1] opens Backup Operations directly (no intermediate hub choices).
    task_menus.run_backup_hub()
    assert called == ["backup_ops"]

    called.clear()
    answers = iter(["1", "2", "", "3", "", "4", "", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_sync_hub()
    assert "sync" in called and "transfer_status" in called
    assert "transfer_history" in called

    called.clear()
    answers = iter(["1", "", "2", "", "3", "", "4", "", "5", "", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_repo_hub()
    assert "offline_sync" in called and "offline_receipt" in called
    assert "repo_status" in called

    called.clear()
    answers = iter(["1", "2", "3", "", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_migration_hub()
    assert "erebus" in called and "destination" in called

    called.clear()
    answers = iter(["1", "2", "3", "4", "", "5", "", "6", "", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_deploy_handoff_hub()
    assert "deploy" in called and "handoff" in called and "receiver" in called
    assert "bundle" in called
    assert "packaging" in called


def test_restore_hub_opens_consolidated_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.run_recovery_dashboard",
        lambda **_k: opened.append("dashboard"),
    )
    monkeypatch.setattr(
        "mercury.deploy.interactive_menu.run_deploy_menu",
        lambda **_k: opened.append("deploy"),
    )
    from mercury.menu.task_menus import run_recovery_hub

    run_recovery_hub()
    assert opened == ["dashboard"]
    assert "deploy" not in opened


def test_direct_cli_routes_unchanged() -> None:
    from mercury.cli import app

    groups = {group.name for group in app.registered_groups}
    expected = {"backup", "sync", "repo", "storage", "deploy", "restore-check", "transfer"}
    assert not (expected - groups)


def test_menu_actions_wire_all_nine_keys() -> None:
    from mercury.menu.actions import menu_actions

    acts = menu_actions()
    assert set(acts) == {str(i) for i in range(1, 10)}
    assert acts["1"].action_id == MAIN_BACKUP
    assert acts["2"].action_id == MAIN_SYNC
    assert acts["3"].action_id == MAIN_REPO
    assert acts["4"].action_id == MAIN_STORAGE
    assert acts["5"].action_id == MAIN_RECOVERY
    assert acts["6"].action_id == MAIN_MIGRATION
    assert acts["7"].action_id == MAIN_DEPLOY
    assert acts["8"].action_id == MAIN_REPORTS
    assert acts["9"].action_id == MAIN_HEALTH
