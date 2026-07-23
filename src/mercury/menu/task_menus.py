"""Phase 3 task hubs beneath the main operator console."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen


def _submenu(title: str, options: list[tuple[str, str]]) -> str | None:
    from mercury.terminal.theme import menu_bottom_option, menu_item_line

    display_screen.open_screen(title)
    for key, label in options:
        output.write(menu_item_line(key, label, indent=2))
    output.write(menu_bottom_option("Back", indent=2))
    output.write("")
    choice = (menu_prompts.ask("Choice") or "").strip()
    if choice in {"", "0"}:
        return None
    return choice

def run_backup_sync_hub() -> None:
    """Back up and sync — Phase 2 wizard plus expert subpaths."""
    from mercury.storage.host_maintenance import load_host_maintenance

    while True:
        host = load_host_maintenance()
        title = (
            "Back up and sync again"
            if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
            else "Back up and sync this workstation"
        )
        choice = _submenu(
            title,
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
            continue
        if choice == "2":
            from mercury.backup.interactive_menu import run_production_backup_flow

            run_production_backup_flow()
            continue
        if choice == "3":
            from mercury.backup.interactive_menu import run_development_backup_flow

            run_development_backup_flow()
            continue
        if choice == "4":
            from mercury.repo.interactive_menu import run_offline_repo_menu

            run_offline_repo_menu()
            continue
        if choice == "5":
            from mercury.sync.interactive_menu import run_sync_menu

            run_sync_menu()
            continue
        if choice == "6":
            from mercury.backup.interactive_menu import run_backup_menu

            run_backup_menu()
            continue
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
            continue
        if choice == "2":
            from mercury.recovery.interactive_menu import run_recovery_menu

            run_recovery_menu()
            continue
        if choice == "3":
            run_migration_hub()
            continue
        if choice == "4":
            run_advanced_hub()
            continue
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
            continue
        if choice == "2":
            from mercury.deploy.interactive_menu import run_deploy_menu

            run_deploy_menu()
            continue
        if choice == "3":
            from mercury.recovery.interactive_menu import run_recovery_menu

            run_recovery_menu()
            continue
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
                ("5", "Appearance and theme"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.env.interactive_menu import run_env_menu

            run_env_menu()
            continue
        if choice == "2":
            from mercury.database.discovery_menu import run_discover_menu

            run_discover_menu()
            continue
        if choice == "3":
            from mercury.env.interactive_menu import run_doctor_menu

            run_doctor_menu()
            continue
        if choice == "4":
            from mercury.storage.interactive_menu import run_storage_menu

            run_storage_menu()
            continue
        if choice == "5":
            run_appearance_menu()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_appearance_menu() -> None:
    """Host-local theme selection (no Mercury HDD required)."""
    from mercury.terminal.design_system import clear_style_cache
    from mercury.terminal.theme_preview import print_theme_preview
    from mercury.terminal.theme_settings import (
        THEME_CLASSIC,
        THEME_MONOCHROME,
        THEME_REDLINE,
        active_theme_id,
        list_themes,
        load_theme_selection,
        reset_theme_selection,
        save_theme_selection,
    )

    while True:
        selection = load_theme_selection()
        display_screen.open_screen("Appearance and theme")
        display_screen.write_fields(
            {
                "Active theme": selection.theme_id,
                "Source": selection.source,
                "Settings path": str(selection.path) if selection.path else "(env/default)",
            }
        )
        output.write("")
        for theme_id, display_name, is_active in list_themes():
            mark = "  active" if is_active else ""
            output.write(f"  · {theme_id} — {display_name}{mark}")
        output.write("")
        choice = _submenu(
            "Appearance actions",
            [
                ("1", "Preview Mercury Redline"),
                ("2", "Preview Mercury Classic"),
                ("3", "Preview Monochrome"),
                ("4", f"Set active theme to {THEME_REDLINE}"),
                ("5", f"Set active theme to {THEME_CLASSIC}"),
                ("6", "Reset to default (classic)"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            print_theme_preview(theme_id=THEME_REDLINE)
            menu_prompts.wait_for_continue()
            continue
        if choice == "2":
            print_theme_preview(theme_id=THEME_CLASSIC)
            menu_prompts.wait_for_continue()
            continue
        if choice == "3":
            print_theme_preview(theme_id=THEME_MONOCHROME)
            menu_prompts.wait_for_continue()
            continue
        if choice == "4":
            path = save_theme_selection(THEME_REDLINE)
            clear_style_cache()
            display_screen.write_summary(f"Theme set to {THEME_REDLINE} at {path}")
            menu_prompts.wait_for_continue()
            continue
        if choice == "5":
            path = save_theme_selection(THEME_CLASSIC)
            clear_style_cache()
            display_screen.write_summary(f"Theme set to {THEME_CLASSIC} at {path}")
            menu_prompts.wait_for_continue()
            continue
        if choice == "6":
            reset_theme_selection()
            clear_style_cache()
            display_screen.write_summary(f"Theme reset. Active: {active_theme_id()}")
            menu_prompts.wait_for_continue()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_destination_rehearsal_hub() -> None:
    """Package-driven destination-move hub (read-only; disconnect when ready)."""
    from mercury.menu.destination_move import (
        HUB_ADVANCED_HANDOFF,
        HUB_DESTINATION_STATUS,
        HUB_RECEIVER_GUIDE,
        HUB_REVIEW_PACKAGE,
        HUB_SAFE_DISCONNECT,
        build_destination_hub_options,
        build_destination_move_status,
        print_destination_move_status,
        print_package_receiver_guide,
    )
    from mercury.storage.host_maintenance import load_host_maintenance

    while True:
        host = load_host_maintenance()
        status = build_destination_move_status(host=host)
        display_screen.open_screen("DESTINATION MOVE")
        print_destination_move_status(status, with_title=False)
        from mercury.terminal.theme import menu_item_line

        output.write("")
        options = build_destination_hub_options(host=host)
        for key, label, _action in options:
            output.write(menu_item_line(key, label, indent=2))
        output.write(menu_item_line("0", "Back", indent=2))
        output.write("")
        choice = (menu_prompts.ask("Choice") or "").strip()
        if choice in {"", "0"}:
            return
        action_id = next((a for k, _l, a in options if k == choice), None)
        if action_id is None:
            output.write(menu_prompts.invalid_choice_message(choice))
            continue
        if action_id == HUB_SAFE_DISCONNECT:
            from mercury.storage.interactive_menu import run_safe_disconnect_wizard

            run_safe_disconnect_wizard()
            continue
        if action_id == HUB_REVIEW_PACKAGE:
            from mercury.storage.interactive_menu import run_storage_menu

            display_screen.write_summary(
                f"Current package: {status.package_id}"
            )
            run_storage_menu()
            continue
        if action_id == HUB_RECEIVER_GUIDE:
            print_package_receiver_guide(package_id=status.package_id)
            continue
        if action_id == HUB_DESTINATION_STATUS:
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu()
            continue
        if action_id == HUB_ADVANCED_HANDOFF:
            from mercury.handoff.interactive_menu import run_advanced_handoff_tools

            run_advanced_handoff_tools()
            continue


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
            continue
        if choice == "2":
            from mercury.sync.interactive_menu import run_sync_menu

            run_sync_menu()
            continue
        if choice == "3":
            from mercury.repo.interactive_menu import run_offline_repo_menu

            run_offline_repo_menu()
            continue
        if choice == "4":
            from mercury.storage.interactive_menu import run_storage_menu

            run_storage_menu()
            continue
        if choice == "5":
            from mercury.deploy.interactive_menu import run_deploy_menu

            run_deploy_menu()
            continue
        if choice == "6":
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu(interactive=True)
            continue
        output.write(menu_prompts.invalid_choice_message(choice))
