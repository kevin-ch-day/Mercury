"""Nine-area task hubs beneath the main operator console."""

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
        "Observe-only. Invalid maintenance receipts are not backup/handoff evidence. "
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
    """[1] Backup and verification — opens Backup Operations directly."""
    from mercury.backup.interactive_menu import run_backup_menu

    run_backup_menu()


# Compatibility name used by older runners/tests.
run_backup_sync_hub = run_backup_hub


def run_sync_hub() -> None:
    """[2] Database sync and data movement."""
    while True:
        choice = _submenu(
            "Database sync and data movement",
            [
                ("1", "Sync readiness and execution"),
                ("2", "Transfer package status"),
                ("3", "Transfer / handoff history"),
                ("4", "How to write or receive a transfer package"),
            ],
            purpose=(
                "Move data between databases and workstations. Sync plans are "
                "policy-gated; transfer write/receive stay CLI-first."
            ),
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
            _pause()
            continue
        if choice == "3":
            from mercury.handoff.history import build_handoff_history
            from mercury.handoff.terminal import print_handoff_history

            print_handoff_history(build_handoff_history())
            _pause()
            continue
        if choice == "4":
            _command_card(
                "Transfer package commands",
                [
                    "Inspect:  ./run.sh transfer status",
                    "History:  ./run.sh transfer history",
                    "Write:    ./run.sh transfer write [--execute]",
                    "Receive:  ./run.sh transfer receive",
                    "",
                    "Omit --execute on write for a dry-run plan. "
                    "Handoff packaging also lives under Deployment and handoff [7].",
                ],
            )
            _pause()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))


def run_repo_hub(*, interactive: bool = True) -> None:
    """[3] Git and repository recovery — offline status and actions on one screen."""
    from mercury.repo.interactive_menu import (
        offline_clone_plan,
        run_offline_sync_now,
        show_offline_sync_receipt,
    )
    from mercury.repo.offline_terminal import print_offline_clone_plan
    from mercury.terminal.theme import menu_bottom_option, menu_item_line

    while True:
        display_screen.open_screen("Git and repository recovery")
        display_screen.write_summary(
            "Offline HDD clones, repository status, and Git bundle planning. "
            "Bundle execute remains CLI-gated so dirty worktrees stay explicit."
        )
        display_screen.write_blank()
        print_offline_clone_plan(offline_clone_plan(), with_title=False)
        display_screen.write_blank()
        options = [
            ("1", "Sync offline GitHub repositories"),
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
    """[5] Restore and Disaster Recovery — consolidated dashboard."""
    from mercury.restore.interactive_dashboard import run_recovery_dashboard

    run_recovery_dashboard()


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
            purpose=(
                "Capture and validate workstation move packages. Handoff "
                "packaging and deploy live under Deployment and handoff [7]; "
                "storage cutover under Mercury HDD and storage [4]."
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
    """[7] Deployment and handoff."""
    while True:
        choice = _submenu(
            "Deployment and handoff",
            [
                ("1", "System deployment"),
                ("2", "Workstation handoff status"),
                ("3", "Handoff packaging tools"),
                ("4", "Write DB bundle and runbooks"),
                ("5", "Production cutover commands"),
                ("6", "Receiving workstation guide"),
            ],
            purpose=(
                "Package evidence for the next host and deploy sealed "
                "artifacts. Production cutover execute stays CLI-gated."
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
                    "Restore and disaster recovery [5].",
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
                ("6", "Show local configuration"),
            ],
            purpose=(
                "Host readiness, inventory, and doctor. Storage lifecycle "
                "belongs under Mercury HDD and storage [4]."
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
                "Lifecycle, cleanup, and detach: Main Menu → "
                "Mercury HDD and storage [4]."
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
