"""Interactive Backup and Sync session wizard (Phase 2)."""

from __future__ import annotations

from mercury import output
from mercury.backup.session_models import (
    BackupSyncSession,
    SessionPlan,
    SessionResult,
    recommended_session_plan,
)
from mercury.backup.session_receipt import render_session_summary_text
from mercury.backup.session_runner import SessionHooks, run_backup_sync_session
from mercury.menu import prompts as menu_prompts
from mercury.storage.host_maintenance import load_host_maintenance
from mercury.storage.operation_availability import (
    assess_operation_availability,
    ensure_backup_writes_available,
    format_hard_block_message,
    format_recoverable_prompt,
    format_strong_prompt,
)
from mercury.terminal import screen as display_screen


def _print_overview(*, availability_classification: str) -> None:
    host = load_host_maintenance()
    display_screen.open_screen("Back up and sync this workstation")
    package = host.package_verification_status or "Pending"
    if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED":
        if host.source_writes_resumed_after_package:
            package = "VERIFIED · rehearsal snapshot"
        elif host.destination_rehearsal_active or host.destination_rehearsal_in_progress:
            package = "VERIFIED · destination rehearsal"
        else:
            package = "VERIFIED · rehearsal snapshot"
    write_state = (
        "Enabled · primary"
        if host.writes_allowed and host.active_write_role == "primary"
        else "Disabled · disconnect preparation"
        if host.storage_availability == "detaching" or host.source_detach_preparation
        else "Disabled"
    )
    display_screen.write_fields(
        {
            "Mercury HDD": "Connected · mounted",
            "Write state": write_state,
            "Package": package,
            "Recovery": (
                "New backup artifacts created after package"
                if getattr(host, "recovery_artifacts_created_after_package", False)
                or host.source_changed_since_package
                else "No new recovery artifacts after package"
            ),
            "Source data": (
                "Production source data changed after package"
                if getattr(host, "source_data_changed_since_package", False)
                else "No known production changes after package"
            ),
            "Availability": availability_classification,
        }
    )
    display_screen.write_blank()
    if host.destination_rehearsal_active or host.destination_rehearsal_in_progress:
        display_screen.write_summary(
            "A verified destination rehearsal package exists."
        )
        display_screen.write_summary(
            "Restoring source writes will allow new source changes that are not included "
            "in that package. The package remains valid for rehearsal, but it will no "
            "longer represent the newest source state."
        )
        display_screen.write_blank()
    display_screen.write_summary("Planned session:")
    display_screen.write_fields(
        {
            "Production database backup": "Yes",
            "Verify new production backups": "Yes",
            "Development database backup": "Optional",
            "Offline Git recovery capture": "Yes",
            "Production-to-development sync": "Optional",
        }
    )


def _choice_menu() -> str | None:
    output.write("")
    output.write("  [1] Restore source writer and run recommended session")
    output.write("  [2] Review or customize session")
    output.write("  [3] Databases only")
    output.write("  [4] Git recovery only")
    output.write("  [0] Cancel")
    output.write("")
    return menu_prompts.ask("Choice").strip() or None


def _customize_plan(plan: SessionPlan) -> SessionPlan | None:
    current = plan.model_copy(deep=True)
    while True:
        display_screen.open_screen("Customize Backup and Sync")
        flags = [
            ("Back up production databases", current.production_backup),
            ("Verify newly written production backups", current.verify_production),
            ("Back up development databases", current.development_backup),
            ("Capture offline Git recovery", current.git_recovery),
            ("Sync production databases to development", current.sync_development),
            ("Restore-check newly written backups", current.restore_check),
        ]
        for label, enabled in flags:
            mark = "x" if enabled else " "
            output.write(f"  [{mark}] {label}")
        output.write("")
        output.write("  [1] Toggle production backup")
        output.write("  [2] Toggle development backup")
        output.write("  [3] Toggle Git recovery")
        output.write("  [4] Toggle production-to-development sync")
        output.write("  [5] Toggle restore-check")
        output.write("  [6] Run selected session")
        output.write("  [0] Cancel")
        choice = menu_prompts.ask("Choice").strip()
        if choice == "0":
            return None
        if choice == "1":
            current.production_backup = not current.production_backup
            if current.production_backup:
                current.verify_production = True
            else:
                current.sync_development = False
        elif choice == "2":
            current.development_backup = not current.development_backup
        elif choice == "3":
            current.git_recovery = not current.git_recovery
        elif choice == "4":
            if not current.production_backup:
                display_screen.write_status(
                    "fail",
                    "Production-to-development sync requires production backup in this session.",
                )
            else:
                current.sync_development = not current.sync_development
        elif choice == "5":
            current.restore_check = not current.restore_check
        elif choice == "6":
            if not (
                current.production_backup
                or current.git_recovery
                or current.development_backup
            ):
                display_screen.write_status(
                    "fail",
                    "Select at least one of: production backup, development backup, or Git recovery.",
                )
                continue
            return current.normalize()
        else:
            output.write(menu_prompts.invalid_choice_message(choice))


def _ask_optional_lanes(plan: SessionPlan) -> SessionPlan:
    current = plan.model_copy(deep=True)
    if current.production_backup:
        include_dev = menu_prompts.ask_yes_no(
            "Also back up configured development databases for migration recovery?",
            default=False,
        )
        current.development_backup = include_dev is True
        include_sync = menu_prompts.ask_yes_no(
            "Sync production databases into development after verified production backup?",
            default=False,
        )
        current.sync_development = include_sync is True
    return current.normalize()


def print_session_result(session: BackupSyncSession) -> None:
    title = {
        SessionResult.PASS: "BACKUP AND SYNC COMPLETE",
        SessionResult.PARTIAL: "BACKUP AND SYNC PARTIAL",
        SessionResult.FAIL: "BACKUP AND SYNC FAILED",
        SessionResult.REFUSED: "BACKUP AND SYNC REFUSED",
        SessionResult.CANCELLED: "BACKUP AND SYNC CANCELLED",
    }.get(session.session_result, "BACKUP AND SYNC RESULT")
    display_screen.open_screen(title)
    output.write(render_session_summary_text(session))
    output.write("")
    host = load_host_maintenance()
    if host.writes_allowed or host.source_writes_resumed_after_package:
        display_screen.write_summary("Source state")
        display_screen.write_summary(
            "  Backups enabled" if host.writes_allowed else "  Writes disabled"
        )
        if host.source_writes_resumed_after_package:
            display_screen.write_summary(
                "  Source writes resumed after destination package"
            )
            if getattr(host, "recovery_artifacts_created_after_package", False) or host.source_changed_since_package:
                display_screen.write_summary(
                    "  Recovery: new backup artifacts created after package"
                )
            else:
                display_screen.write_summary(
                    "  Recovery: no new recovery artifacts after package"
                )
            if getattr(host, "source_data_changed_since_package", False):
                display_screen.write_summary(
                    "  Source data: production changes after package"
                )
            else:
                display_screen.write_summary(
                    "  Source data: no known production changes after package"
                )
            display_screen.write_summary(
                "  Current package remains a rehearsal snapshot (not invalidated)"
            )


def offer_post_session_actions(session: BackupSyncSession) -> str | None:
    output.write("")
    output.write("What would you like to do next?")
    allow_disconnect = session.session_result in {
        SessionResult.PASS,
        SessionResult.PARTIAL,
    }
    if allow_disconnect:
        output.write("  [1] Safely disconnect Mercury HDD")
    output.write("  [2] Review session details")
    output.write("  [3] Return to main menu")
    output.write("  [0] Exit Mercury")
    choice = menu_prompts.ask("Choice").strip()
    if choice == "1" and allow_disconnect:
        return "safe_disconnect"
    if choice == "2":
        return "review"
    if choice == "3":
        return "main_menu"
    if choice == "0":
        return "exit"
    return None


def _ensure_writer_ready(*, hooks: SessionHooks | None) -> bool:
    """Return True when backup writes are available after optional guided restore."""
    availability = assess_operation_availability("database_backup")
    if availability.is_hard_block:
        output.write("")
        output.write(format_hard_block_message(availability))
        return False
    if availability.available:
        return True
    if availability.is_strong:
        output.write("")
        output.write(format_strong_prompt(availability))
    elif availability.is_recoverable:
        output.write("")
        output.write(format_recoverable_prompt(availability))
    ensure = (
        hooks.ensure_writes
        if hooks is not None and hooks.ensure_writes is not None
        else ensure_backup_writes_available
    )
    restored = ensure(interactive=True, write=output.write)
    return bool(restored.available)


def run_backup_sync_wizard(
    *,
    interactive: bool = True,
    hooks: SessionHooks | None = None,
) -> BackupSyncSession | None:
    """Interactive Backup and Sync entry point."""
    if not interactive:
        return run_backup_sync_session(preview=True, execute=False, interactive=False)

    availability = assess_operation_availability("database_backup")
    _print_overview(availability_classification=availability.classification.value)

    if availability.is_hard_block:
        output.write("")
        output.write(format_hard_block_message(availability))
        session = run_backup_sync_session(execute=True, interactive=True, hooks=hooks)
        print_session_result(session)
        return session

    choice = _choice_menu()
    if choice in {None, "0"}:
        display_screen.write_summary("Backup and Sync cancelled.")
        return None

    plan = recommended_session_plan()
    if choice == "2":
        customized = _customize_plan(plan)
        if customized is None:
            display_screen.write_summary("Backup and Sync cancelled.")
            return None
        plan = customized
    elif choice == "3":
        plan = SessionPlan(
            production_backup=True,
            verify_production=True,
            development_backup=False,
            git_recovery=False,
            sync_development=False,
        ).normalize()
    elif choice == "4":
        plan = SessionPlan(
            production_backup=False,
            verify_production=False,
            development_backup=False,
            git_recovery=True,
            sync_development=False,
        ).normalize()
    elif choice != "1":
        output.write(menu_prompts.invalid_choice_message(choice))
        return None

    # Storage first — never ask optional lanes while writer is blocked.
    if not _ensure_writer_ready(hooks=hooks):
        display_screen.write_summary("Backup and Sync cancelled. Mercury writes remain disabled.")
        return None

    if choice in {"1", "3"}:
        plan = _ask_optional_lanes(plan)
        if choice == "3":
            plan.git_recovery = False

    # Writer already restored; runner should see AVAILABLE and continue.
    session = run_backup_sync_session(
        plan=plan,
        execute=True,
        interactive=True,
        hooks=hooks,
    )
    print_session_result(session)
    action = offer_post_session_actions(session)
    if action == "safe_disconnect":
        from mercury.storage.interactive_menu import run_safe_disconnect_wizard

        run_safe_disconnect_wizard()
    elif action == "review":
        output.write(render_session_summary_text(session))
        if session.exact_artifact_ids:
            display_screen.write_summary(
                "Exact artifact IDs: " + ", ".join(session.exact_artifact_ids)
            )
    elif action == "exit":
        raise SystemExit(0)
    return session
