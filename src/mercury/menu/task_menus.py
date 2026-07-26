"""Nine-area task hubs beneath the main operator console."""

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


def _show_full_backup_receipts() -> None:
    """Observe-only full-backup receipt classification (same surface as CLI plan)."""
    from mercury.backup.full_backup_receipts import (
        INVALID_MAINTENANCE_CLASS,
        plan_quarantine_invalid_full_backup_receipts,
    )
    from mercury.core.usb_mount import resolve_operator_mount

    mount = resolve_operator_mount()
    plan = plan_quarantine_invalid_full_backup_receipts(mount)
    display_screen.open_screen("Full-backup receipt plan")
    display_screen.write_fields(
        {
            "Mount": str(plan.mount_root),
            "Quarantine dir": str(plan.quarantine_dir),
            "Governed": plan.governed_count,
            "Invalid maintenance": plan.invalid_count,
            "Total scanned": len(plan.entries),
        }
    )
    display_screen.write_blank()
    if not plan.entries:
        display_screen.write_summary("No full_backup_runs receipts found.")
        return
    rows = [
        [
            entry.run_id,
            entry.classification,
            entry.outcome or "-",
            str(entry.overall_written),
        ]
        for entry in plan.entries
    ]
    display_screen.write_compact_table(
        ["RUN ID", "CLASS", "OUTCOME", "WRITTEN"],
        rows,
        min_col_widths=[28, 18, 10, 8],
        max_col_widths=[40, 28, 12, 8],
    )
    display_screen.write_blank()
    display_screen.write_summary(
        "Observe-only. Invalid maintenance receipts are not backup/handoff evidence. "
        f"Later quarantine target: {plan.quarantine_dir} "
        f"(class={INVALID_MAINTENANCE_CLASS})."
    )


def run_backup_hub() -> None:
    """[1] Backup and verification."""
    while True:
        choice = _submenu(
            "Backup and verification",
            [
                ("1", "Run guided backup session"),
                ("2", "Open Backup Operations menu"),
                ("3", "Verify backups"),
                ("4", "Full-backup receipts (observe-only)"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.backup.session_wizard import run_backup_sync_wizard

            run_backup_sync_wizard()
            continue
        if choice == "2":
            from mercury.backup.interactive_menu import run_backup_menu

            run_backup_menu()
            continue
        if choice == "3":
            from mercury.verify.interactive_menu import run_verify_menu

            run_verify_menu()
            continue
        if choice == "4":
            _show_full_backup_receipts()
            menu_prompts.ask("Press Enter to continue")
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


# Compatibility name used by older runners/tests.
run_backup_sync_hub = run_backup_hub


def run_sync_hub() -> None:
    """[2] Database sync and data movement."""
    while True:
        choice = _submenu(
            "Database sync and data movement",
            [
                ("1", "Production-to-development sync"),
                ("2", "Transfer package status"),
                ("3", "Transfer CLI hint (write / receive)"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.sync.interactive_menu import run_sync_menu

            run_sync_menu()
            continue
        if choice == "2":
            from mercury.core.runtime import should_probe_database_status
            from mercury.transfer import build_transfer_bundle, print_transfer_bundle

            print_transfer_bundle(
                build_transfer_bundle(live=should_probe_database_status())
            )
            menu_prompts.ask("Press Enter to continue")
            continue
        if choice == "3":
            display_screen.write_summary(
                "Controlled transfer execute paths remain CLI-first: "
                "./run.sh transfer status | transfer write | transfer receive. "
                "Handoff packaging lives under Deployment and handoff [7]."
            )
            menu_prompts.ask("Press Enter to continue")
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_repo_hub() -> None:
    """[3] Git and repository recovery."""
    while True:
        choice = _submenu(
            "Git and repository recovery",
            [
                ("1", "Offline GitHub recovery"),
                ("2", "Repository status"),
                ("3", "Repository bundle CLI hint"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.repo.interactive_menu import run_offline_repo_menu

            run_offline_repo_menu()
            continue
        if choice == "2":
            from mercury.repo import inspect_repositories, load_repo_definitions
            from mercury.repo.terminal import print_repo_statuses

            print_repo_statuses(inspect_repositories(load_repo_definitions()))
            menu_prompts.ask("Press Enter to continue")
            continue
        if choice == "3":
            display_screen.write_summary(
                "Bundle create/verify/restore: ./run.sh repo bundle [--execute] "
                "and ./run.sh repo status. Dirty worktree capture is reported in "
                "repo status / transfer status; committing remains outside Mercury."
            )
            menu_prompts.ask("Press Enter to continue")
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_restore_tools_hub() -> None:
    """Restore-specific tools only (never opens general expert routing)."""
    while True:
        choice = _submenu(
            "Restore tools",
            [
                ("1", "Restore-check operations"),
                ("2", "Failed restore-check cleanup"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.restore.interactive_menu import run_restore_menu

            run_restore_menu()
            continue
        if choice == "2":
            from mercury.restore.interactive_menu import run_restore_menu

            # Cleanup is option [3] inside restore-check ops when temp DBs exist.
            display_screen.write_summary(
                "Open Restore-check operations and choose cleanup when "
                "_restorecheck_* databases are listed. CLI: "
                "./run.sh restore-check cleanup."
            )
            run_restore_menu()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_recovery_hub() -> None:
    """[5] Restore and disaster recovery."""
    while True:
        choice = _submenu(
            "Restore and disaster recovery",
            [
                ("1", "Restore-check operations"),
                ("2", "Disaster recovery planning"),
                ("3", "Restore tools"),
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
            run_restore_tools_hub()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_migration_hub() -> None:
    """[6] Workstation migration (capture / package / destination validation)."""
    while True:
        choice = _submenu(
            "Workstation migration",
            [
                ("1", "Source capture → Capture Erebus source"),
                ("2", "Destination move / package validation"),
                ("3", "Migration readiness"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.migration.erebus_capture.menu import run_erebus_source_capture_menu

            run_erebus_source_capture_menu()
            continue
        if choice == "2":
            run_destination_rehearsal_hub()
            continue
        if choice == "3":
            from mercury.migration.readiness import build_migration_readiness
            from mercury.migration.terminal import print_migration_blockers, print_migration_next

            report = build_migration_readiness()
            print_migration_blockers(report)
            print_migration_next(report)
            menu_prompts.ask("Press Enter to continue")
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_deploy_handoff_hub() -> None:
    """[7] Deployment and handoff."""
    while True:
        choice = _submenu(
            "Deployment and handoff",
            [
                ("1", "System deployment"),
                ("2", "Workstation handoff"),
                ("3", "Production cutover preview (observe-only)"),
                ("4", "Receiving workstation guide"),
            ],
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.deploy.interactive_menu import run_deploy_menu

            run_deploy_menu()
            continue
        if choice == "2":
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu(interactive=True)
            continue
        if choice == "3":
            display_screen.write_summary(
                "Sealed-package production cutover remains CLI-gated: "
                "./run.sh production-cutover preview "
                "then production-cutover execute (approval required). "
                "Acceptance/rollback evidence is recorded by those commands."
            )
            menu_prompts.ask("Press Enter to continue")
            continue
        if choice == "4":
            from mercury.handoff.receiver import build_receiver_handoff_guide
            from mercury.handoff.terminal import print_receiver_handoff_guide

            print_receiver_handoff_guide(checklist=build_receiver_handoff_guide())
            menu_prompts.ask("Press Enter to continue")
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_health_hub() -> None:
    """[9] System health and configuration."""
    while True:
        choice = _submenu(
            "System health and configuration",
            [
                ("1", "Environment details"),
                ("2", "Database inventory"),
                ("3", "System doctor and repair guide"),
                ("4", "Storage status summary (observe-only)"),
                ("5", "Appearance and theme"),
                ("6", "Local configuration CLI hint"),
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
            from mercury.storage.report import build_storage_status_report
            from mercury.storage.terminal import print_storage_status

            print_storage_status(build_storage_status_report())
            output.write("")
            output.write(
                "Lifecycle, cleanup, and detach: Main Menu → Mercury HDD and storage [4]."
            )
            menu_prompts.ask("Press Enter to continue")
            continue
        if choice == "5":
            from mercury.menu.options_menu import run_appearance_menu

            run_appearance_menu()
            continue
        if choice == "6":
            display_screen.write_summary(
                "Local config: ./run.sh config init | config show. "
                "Never commit config/local.toml or passwords."
            )
            menu_prompts.ask("Press Enter to continue")
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_appearance_menu() -> None:
    """Compatibility wrapper — shared Options appearance workflow."""
    from mercury.menu.options_menu import run_appearance_menu as _run

    _run()


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
