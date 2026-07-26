"""Advanced-tools hub removed — capabilities remain on proper hubs (routing only)."""

from __future__ import annotations

from unittest.mock import MagicMock

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
    main_menu_option_by_action,
)


def test_advanced_main_menu_entry_gone() -> None:
    assert main_menu_max_primary_actions() == 6
    titles = " ".join(t for _k, t in main_menu_items(writes_allowed=True))
    assert "Advanced tools" not in titles
    assert "Advanced software-only" not in " ".join(
        t for _k, t in main_menu_items(software_only=True)
    )
    keys = [k for k, _t in main_menu_items(writes_allowed=True)]
    assert keys == ["1", "2", "3", "4", "5", "6"]
    # Obsolete id still resolves for hints, but not as its own top-level slot.
    key, title = main_menu_option_by_action(MAIN_ADVANCED)
    assert key == "1"
    assert "Advanced" not in title


def test_former_capabilities_reachable_from_proper_hubs(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    monkeypatch.setattr(
        "mercury.backup.session_wizard.run_backup_sync_wizard",
        lambda **_k: called.append("guided"),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_backup_menu",
        lambda **_k: called.append("backup_ops"),
    )
    monkeypatch.setattr(
        "mercury.sync.interactive_menu.run_sync_menu",
        lambda **_k: called.append("sync"),
    )
    monkeypatch.setattr(
        "mercury.repo.interactive_menu.run_offline_repo_menu",
        lambda **_k: called.append("offline_repos"),
    )
    monkeypatch.setattr(
        "mercury.storage.interactive_menu.run_storage_menu",
        lambda **_k: called.append("storage"),
    )
    monkeypatch.setattr(
        "mercury.deploy.interactive_menu.run_deploy_menu",
        lambda **_k: called.append("deploy"),
    )
    monkeypatch.setattr(
        "mercury.handoff.interactive_menu.run_handoff_menu",
        lambda **_k: called.append("handoff"),
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_menu.run_restore_menu",
        lambda **_k: called.append("restore"),
    )
    monkeypatch.setattr(
        "mercury.recovery.interactive_menu.run_recovery_menu",
        lambda **_k: called.append("recovery"),
    )
    monkeypatch.setattr(
        "mercury.verify.interactive_menu.run_verify_menu",
        lambda **_k: called.append("verify"),
    )
    monkeypatch.setattr(
        "mercury.migration.erebus_capture.menu.run_erebus_source_capture_menu",
        lambda **_k: called.append("erebus"),
    )
    monkeypatch.setattr(
        "mercury.storage.host_maintenance.load_host_maintenance",
        lambda: MagicMock(package_verification_status="Pending"),
    )

    from mercury.menu import task_menus

    assert not hasattr(task_menus, "run_advanced_hub")

    # Main 1 — guided, backup ops, sync, offline git
    answers = iter(["1", "2", "3", "4", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_backup_sync_hub()
    assert called == ["guided", "backup_ops", "sync", "offline_repos"]
    called.clear()

    # Main 2 — storage (status/lifecycle/cleanup live inside storage menu)
    answers = iter(["0"])  # unused; storage menu mocked
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers, "0"))
    # Storage is opened directly from main via runners; confirm callable target.
    from mercury.storage.interactive_menu import run_storage_menu

    run_storage_menu()
    assert called == ["storage"]
    called.clear()

    # Main 3 — restore/DR only; restore tools never opens advanced hub
    answers = iter(["1", "2", "3", "1", "0", "2", "0", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_recovery_hub()
    assert called.count("restore") >= 1
    assert "recovery" in called
    assert "backup_ops" not in called
    assert "sync" not in called
    assert "deploy" not in called
    assert "handoff" not in called
    called.clear()

    answers = iter(["3", "2", "0", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_recovery_hub()
    assert called == ["verify"]
    called.clear()

    # Main 5 — handoff, deploy, erebus capture
    answers = iter(["1", "2", "3", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_migration_hub()
    assert called == ["handoff", "deploy", "erebus"]


def test_restore_hub_does_not_open_advanced_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.menu import task_menus

    assert "run_advanced_hub" not in dir(task_menus)
    opened: list[str] = []
    monkeypatch.setattr(
        "mercury.restore.interactive_menu.run_restore_menu",
        lambda **_k: opened.append("restore"),
    )
    monkeypatch.setattr(
        "mercury.verify.interactive_menu.run_verify_menu",
        lambda **_k: opened.append("verify"),
    )
    answers = iter(["3", "0", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_recovery_hub()
    assert opened == []  # entered restore tools then immediately back
    answers = iter(["3", "1", "0", "0"])
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: next(answers))
    task_menus.run_recovery_hub()
    assert opened == ["restore"]


def test_entering_submenus_is_non_destructive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening hubs and pressing Back must not execute backup/sync/deploy/cleanup."""
    executed: list[str] = []

    def _mark(name: str):
        def _inner(*_a, **_k):
            executed.append(name)

        return _inner

    monkeypatch.setattr("mercury.backup.session_wizard.run_backup_sync_wizard", _mark("guided"))
    monkeypatch.setattr("mercury.backup.interactive_menu.run_backup_menu", _mark("backup"))
    monkeypatch.setattr("mercury.sync.interactive_menu.run_sync_menu", _mark("sync"))
    monkeypatch.setattr("mercury.repo.interactive_menu.run_offline_repo_menu", _mark("repos"))
    monkeypatch.setattr("mercury.storage.interactive_menu.run_storage_menu", _mark("storage"))
    monkeypatch.setattr("mercury.deploy.interactive_menu.run_deploy_menu", _mark("deploy"))
    monkeypatch.setattr("mercury.handoff.interactive_menu.run_handoff_menu", _mark("handoff"))
    monkeypatch.setattr(
        "mercury.storage.host_maintenance.load_host_maintenance",
        lambda: MagicMock(package_verification_status="Pending"),
    )

    from mercury.menu import task_menus

    for runner in (
        task_menus.run_backup_sync_hub,
        task_menus.run_recovery_hub,
        task_menus.run_migration_hub,
        task_menus.run_restore_tools_hub,
    ):
        monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "0")
        runner()

    assert executed == []


def test_back_navigation_returns_from_each_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.menu import task_menus

    monkeypatch.setattr(
        "mercury.storage.host_maintenance.load_host_maintenance",
        lambda: MagicMock(package_verification_status="Pending"),
    )
    for runner in (
        task_menus.run_backup_sync_hub,
        task_menus.run_recovery_hub,
        task_menus.run_migration_hub,
        task_menus.run_health_hub,
        task_menus.run_restore_tools_hub,
    ):
        monkeypatch.setattr("mercury.menu.prompts.ask", lambda *_a, **_k: "0")
        runner()  # must return without raising


def test_capability_hint_routing_matrix() -> None:
    assert main_menu_hint(MAIN_BACKUP_SYNC).endswith("[1]")
    assert main_menu_hint(MAIN_STORAGE).endswith("[2]")
    assert main_menu_hint(MAIN_RECOVERY).endswith("[3]")
    assert main_menu_hint(MAIN_REPORTS).endswith("[4]")
    assert main_menu_hint(MAIN_MIGRATION).endswith("[5]")
    assert main_menu_hint(MAIN_HEALTH).endswith("[6]")
    assert main_menu_hint("sync_prod_dev").endswith("[1]")
    assert main_menu_hint("offline_repos").endswith("[1]")
    assert main_menu_hint("system_deployment").endswith("[5]")
    assert main_menu_hint("workstation_handoff").endswith("[5]")
    assert "advanced" not in main_menu_hint("sync_prod_dev").lower()
    assert "advanced" not in main_menu_hint(MAIN_ADVANCED).lower()


def test_direct_cli_routes_unchanged() -> None:
    """Typer app still exposes retained expert command groups."""
    from mercury.cli import app

    registered = {cmd.name for cmd in app.registered_commands}
    groups = {group.name for group in app.registered_groups}
    # Top-level menu remains; expert domains stay as CLI groups.
    assert "menu" in registered or "menu" in groups or any(
        getattr(cmd, "name", None) == "menu" for cmd in getattr(app, "registered_commands", [])
    )
    expected_groups = {"backup", "sync", "repo", "storage", "deploy", "restore-check"}
    missing = expected_groups - groups
    assert not missing, f"missing CLI groups: {sorted(missing)}"
