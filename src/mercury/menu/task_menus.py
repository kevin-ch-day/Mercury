"""Phase 3 task hubs beneath the main operator console."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen


def _submenu(title: str, options: list[tuple[str, str]]) -> str | None:
    display_screen.open_screen(title)
    for key, label in options:
        output.write(f"  [{key}] {label}")
    output.write("  [0] Back")
    output.write("")
    choice = (menu_prompts.ask("Choice") or "").strip()
    if choice in {"", "0"}:
        return None
    return choice


def run_backup_sync_hub() -> None:
    """Back up and sync — Phase 2 wizard plus expert subpaths."""
    while True:
        choice = _submenu(
            "Back up and sync this workstation",
            [
                ("1", "Run guided Backup and Sync session"),
                ("2", "Production database backup only"),
                ("3", "Development database backup only"),
                ("4", "Git recovery only"),
                ("5", "Production-to-development sync"),
                ("6", "Open full Backup Operations menu"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.backup.session_wizard import run_backup_sync_wizard

            run_backup_sync_wizard()
            return
        if choice == "2":
            from mercury.backup.interactive_menu import run_backup_menu

            # Expert production path lives in Backup Operations production-backup action.
            run_backup_menu()
            return
        if choice == "3":
            from mercury.backup.interactive_menu import run_backup_menu

            run_backup_menu()
            return
        if choice == "4":
            from mercury.repo.interactive_menu import run_offline_repo_menu

            run_offline_repo_menu()
            return
        if choice == "5":
            from mercury.sync.interactive_menu import run_sync_menu

            run_sync_menu()
            return
        if choice == "6":
            from mercury.backup.interactive_menu import run_backup_menu

            run_backup_menu()
            return
        output.write(menu_prompts.invalid_choice_message(choice))


def run_recovery_hub() -> None:
    while True:
        choice = _submenu(
            "Restore and disaster recovery",
            [
                ("1", "Restore-check by exact backup ID"),
                ("2", "Disaster recovery planning"),
                ("3", "Open Workstation migration (packages / cutover)"),
                ("4", "Open Advanced restore tools"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.restore.interactive_menu import run_restore_menu

            run_restore_menu()
            return
        if choice == "2":
            from mercury.recovery.interactive_menu import run_recovery_menu

            run_recovery_menu()
            return
        if choice == "3":
            run_migration_hub()
            return
        if choice == "4":
            run_advanced_hub()
            return
        output.write(menu_prompts.invalid_choice_message(choice))


def run_migration_hub() -> None:
    """Consolidate handoff + deployment under workstation migration."""
    while True:
        choice = _submenu(
            "Workstation migration",
            [
                ("1", "Workstation handoff"),
                ("2", "System deployment"),
                ("3", "Disaster recovery / receiver planning"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu(interactive=True)
            return
        if choice == "2":
            from mercury.deploy.interactive_menu import run_deploy_menu

            run_deploy_menu()
            return
        if choice == "3":
            from mercury.recovery.interactive_menu import run_recovery_menu

            run_recovery_menu()
            return
        output.write(menu_prompts.invalid_choice_message(choice))


def run_health_hub() -> None:
    while True:
        choice = _submenu(
            "System health and configuration",
            [
                ("1", "Environment details"),
                ("2", "Database inventory"),
                ("3", "System doctor and repair guide"),
                ("4", "Storage status summary"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.env.interactive_menu import run_env_menu

            run_env_menu()
            return
        if choice == "2":
            from mercury.database.discovery_menu import run_discover_menu

            run_discover_menu()
            return
        if choice == "3":
            from mercury.env.interactive_menu import run_doctor_menu

            run_doctor_menu()
            return
        if choice == "4":
            from mercury.storage.interactive_menu import run_storage_menu

            run_storage_menu()
            return
        output.write(menu_prompts.invalid_choice_message(choice))


def run_advanced_hub() -> None:
    while True:
        choice = _submenu(
            "Advanced tools",
            [
                ("1", "Backup Operations (expert)"),
                ("2", "Sync production to development"),
                ("3", "Offline GitHub repositories"),
                ("4", "Mercury HDD cleanup and advanced storage"),
                ("5", "System deployment (expert)"),
                ("6", "Workstation handoff (expert)"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.backup.interactive_menu import run_backup_menu

            run_backup_menu()
            return
        if choice == "2":
            from mercury.sync.interactive_menu import run_sync_menu

            run_sync_menu()
            return
        if choice == "3":
            from mercury.repo.interactive_menu import run_offline_repo_menu

            run_offline_repo_menu()
            return
        if choice == "4":
            from mercury.storage.interactive_menu import run_storage_menu

            run_storage_menu()
            return
        if choice == "5":
            from mercury.deploy.interactive_menu import run_deploy_menu

            run_deploy_menu()
            return
        if choice == "6":
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu(interactive=True)
            return
        output.write(menu_prompts.invalid_choice_message(choice))
