"""Task hubs beneath the Mercury backup and disaster-recovery console."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen


def _submenu(
    title: str,
    options: list[tuple[str, str]],
    *,
    purpose: str | None = None,
) -> str | None:
    from mercury.terminal.theme import menu_bottom_option, menu_item_line

    display_screen.open_screen(title)
    if purpose:
        display_screen.write_summary(purpose)
        display_screen.write_blank()
    for key, label in options:
        output.write(menu_item_line(key, label, indent=2))
    output.write(menu_bottom_option("Back", indent=2))
    output.write("")
    choice = (menu_prompts.ask("Choice") or "").strip()
    if choice in {"", "0"}:
        return None
    return choice


def _pause() -> None:
    menu_prompts.ask("Press Enter to continue")


def _command_card(title: str, lines: list[str]) -> None:
    """Show a short CLI command card (multiline-safe)."""
    display_screen.open_screen(title)
    for line in lines:
        if line:
            display_screen.write_summary(line)
        else:
            display_screen.write_blank()


def _show_local_configuration() -> None:
    """Observe-only config status (same facts as ``mercury config show``)."""
    from mercury.config.settings import config_status
    from mercury.core.paths import resolve_local_config

    status = config_status()
    display_screen.open_screen("Local configuration")
    fields = {"Config path": str(resolve_local_config())}
    fields.update({key: value for key, value in status.items()})
    display_screen.write_fields(fields)
    display_screen.write_blank()
    display_screen.write_summary("Never commit config/local.toml or passwords.")
    display_screen.write_summary("Initialize missing files: ./run.sh config init")


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
        "Observe-only. Invalid maintenance receipts are not backup evidence. "
        f"Later quarantine target: {plan.quarantine_dir} "
        f"(class={INVALID_MAINTENANCE_CLASS})."
    )


def _show_repo_bundle_plan() -> None:
    """Dry-run repository bundle plan (execute remains CLI-gated)."""
    from mercury.repo import (
        build_repo_bundle_plan,
        inspect_repositories,
        load_repo_bundle_settings,
        load_repo_definitions,
    )
    from mercury.repo.terminal import print_repo_bundle_plan

    plan = build_repo_bundle_plan(
        inspect_repositories(load_repo_definitions()),
        load_repo_bundle_settings(),
    )
    print_repo_bundle_plan(plan, executed=False)
    display_screen.write_blank()
    display_screen.write_summary(
        "Preview only. To write bundles: ./run.sh repo bundle --execute"
    )


def run_backup_hub() -> None:
    """[1] Backup production — opens Backup Operations directly."""
    from mercury.backup.interactive_menu import run_backup_menu

    run_backup_menu()


def run_sync_hub() -> None:
    """[5] Prod-to-dev sync — opens readiness and execution directly."""
    from mercury.sync.interactive_menu import run_sync_menu

    run_sync_menu()


def run_repo_hub(*, interactive: bool = True) -> None:
    """[2] Repository backups — offline copies and bundle planning."""
    from mercury.repo.interactive_menu import (
        offline_clone_plan,
        run_offline_sync_now,
        show_offline_sync_receipt,
    )
    from mercury.repo.offline_terminal import print_offline_clone_plan
    from mercury.terminal.theme import menu_bottom_option, menu_item_line

    while True:
        display_screen.open_screen("Repository backups")
        display_screen.write_summary(
            "Keep Git history on backup storage (offline clones and bundle plans). "
            "Source worktrees stay untouched. Bundle execute remains CLI-gated."
        )
        display_screen.write_blank()
        print_offline_clone_plan(offline_clone_plan(), with_title=False)
        display_screen.write_blank()
        options = [
            ("1", "Update repository backups on storage"),
            ("2", "View last sync receipt"),
            ("3", "Repository status"),
            ("4", "Preview repository bundle plan"),
            ("5", "How to create or restore Git bundles"),
        ]
        for key, label in options:
            output.write(menu_item_line(key, label, indent=2))
        output.write(menu_bottom_option("Back", indent=2))
        output.write("")
        if not interactive:
            return
        choice = (menu_prompts.ask("Choice") or "").strip()
        if choice in {"", "0"}:
            return
        if choice == "1":
            run_offline_sync_now()
            menu_prompts.ask("Press Enter to continue")
            continue
        if choice == "2":
            show_offline_sync_receipt()
            menu_prompts.ask("Press Enter to continue")
            continue
        if choice == "3":
            from mercury.repo import inspect_repositories, load_repo_definitions
            from mercury.repo.terminal import print_repo_statuses

            print_repo_statuses(inspect_repositories(load_repo_definitions()))
            _pause()
            continue
        if choice == "4":
            _show_repo_bundle_plan()
            _pause()
            continue
        if choice == "5":
            _command_card(
                "Repository bundle commands",
                [
                    "Status:   ./run.sh repo status",
                    "Preview:  ./run.sh repo bundle",
                    "Write:    ./run.sh repo bundle --execute",
                    "",
                    "Dirty worktree capture is reported in repo/transfer status; "
                    "committing remains outside Mercury.",
                ],
            )
            _pause()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_restore_tools_hub() -> None:
    """Restore-specific tools only (never opens general expert routing)."""
    while True:
        choice = _submenu(
            "Restore tools",
            [
                ("1", "Restore-check operations"),
                ("2", "Clean up temp restore-check databases"),
            ],
            purpose=(
                "Validate backups into disposable _restorecheck_* databases. "
                "Never restores into *_prod."
            ),
        )
        if choice is None:
            return
        if choice == "1":
            from mercury.restore.interactive_menu import run_restore_menu

            run_restore_menu()
            continue
        if choice == "2":
            from mercury.restore.interactive_menu import run_restorecheck_cleanup

            run_restorecheck_cleanup()
            _pause()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_recovery_hub() -> None:
    """[3] Disaster recovery — restore-check and recover this host."""
    from mercury.restore.interactive_dashboard import run_recovery_dashboard

    run_recovery_dashboard()


def run_migration_hub() -> None:
    """Rare workstation-move tools (not part of routine backup/DR)."""
    while True:
        choice = _submenu(
            "Workstation migration",
            [
                ("1", "Source capture → Capture Erebus source"),
                ("2", "Destination move / package validation"),
                ("3", "Migration readiness"),
            ],
            purpose=(
                "Rare host-move capture and package validation. Routine backup and "
                "restore live under Backup production [1] and Disaster recovery [3]. "
                "Storage lifecycle is Backup storage [4]."
            ),
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
            _pause()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_deploy_handoff_hub() -> None:
    """Deploy onto this host and rare handoff packaging."""
    while True:
        choice = _submenu(
            "Deployment and packaging",
            [
            ("1", "Deploy backups onto this host"),
            ("2", "Handoff status"),
                ("3", "Handoff packaging tools"),
                ("4", "Write DB bundle and runbooks"),
                ("5", "Production cutover commands"),
                ("6", "Receiving workstation guide"),
            ],
            purpose=(
                "Restore verified backups onto this MariaDB host, or package "
                "evidence for another machine. Production cutover execute stays CLI-gated."
            ),
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
            from mercury.handoff.interactive_menu import run_advanced_handoff_tools

            run_advanced_handoff_tools()
            continue
        if choice == "4":
            from mercury.backup.interactive_menu import run_write_database_bundle

            run_write_database_bundle()
            _pause()
            continue
        if choice == "5":
            _command_card(
                "Production cutover",
                [
                    "Exact sealed-package promotion (never 'latest').",
                    "",
                    "Preview (observe-only):",
                    "  ./run.sh production-cutover preview \\",
                    "    --package-root <path> --package-id <id> \\",
                    "    --android-backup-id <id> --erebus-backup-id <id> \\",
                    "    --android-source-schema <name> --erebus-source-schema <name> \\",
                    "    --android-target-schema android_permission_intel \\",
                    "    --erebus-target-schema erebus_threat_intel_prod \\",
                    "    --receipt-root <path>",
                    "",
                    "Execute requires an approved preview receipt and:",
                    "  --confirm 'PROMOTE SEALED DESTINATION PACKAGE'",
                    "",
                    "Pinned destination recovery (non-prod schemas) is under "
                    "Disaster recovery [3].",
                ],
            )
            _pause()
            continue
        if choice == "6":
            from mercury.handoff.receiver import build_receiver_handoff_guide
            from mercury.handoff.terminal import print_receiver_handoff_guide

            print_receiver_handoff_guide(checklist=build_receiver_handoff_guide())
            _pause()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_health_hub() -> None:
    """[7] System health and configuration."""
    while True:
        choice = _submenu(
            "System health",
            [
                ("1", "Environment details"),
                ("2", "Database inventory"),
                ("3", "System doctor and repair guide"),
                ("4", "Storage status summary (observe-only)"),
                ("5", "Appearance and theme"),
                ("6", "Show local configuration"),
                ("7", "Deploy backups onto this host"),
                ("8", "Workstation move and handoff (rare)"),
            ],
            purpose=(
                "Host readiness, inventory, and doctor. Backup storage lifecycle "
                "is Backup storage [4]. Deploy/handoff here are uncommon on this always-on host."
            ),
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
                "Lifecycle, cleanup, and detach: Main Menu → Backup storage [4]."
            )
            _pause()
            continue
        if choice == "5":
            from mercury.menu.options_menu import run_appearance_menu

            run_appearance_menu()
            continue
        if choice == "6":
            _show_local_configuration()
            _pause()
            continue
        if choice == "7":
            from mercury.deploy.interactive_menu import run_deploy_menu

            run_deploy_menu()
            continue
        if choice == "8":
            run_migration_hub()
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
