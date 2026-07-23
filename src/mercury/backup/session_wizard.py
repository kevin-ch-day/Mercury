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
)
from mercury.terminal import screen as display_screen
from mercury.terminal.theme import dashboard_row, menu_item_line, rule_line, section_title
from mercury.terminal.theme import colors_enabled


def _package_verified(host) -> bool:
    return host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"


def _package_status_label(host) -> str:
    if not _package_verified(host):
        status = (host.package_verification_status or "").strip()
        if not status or status.upper() in {"PENDING", "UNKNOWN"}:
            return "Not verified"
        # Never dump raw enum tokens into the operator console.
        if status == "DESTINATION_PACKAGE_VERIFIED":
            return "VERIFIED · destination rehearsal"
        return "Not verified"
    pkg = (host.package_id or "").lower()
    if "phase3b" in pkg:
        return "VERIFIED · Phase 3B rehearsal"
    if "final_source" in pkg:
        return "VERIFIED · final source rehearsal"
    if host.source_writes_resumed_after_package:
        return "VERIFIED · rehearsal snapshot"
    return "VERIFIED · destination rehearsal"


def _package_snapshot_label(host) -> str | None:
    """Readable local package creation time from package_id, when present."""
    from mercury.terminal.format import format_package_id_snapshot

    return format_package_id_snapshot(host.package_id or "")


def _operator_action_required(classification: str) -> str | None:
    """Map internal availability enums to operator-facing language."""
    mapping = {
        "STRONG_CONFIRMATION": "Exact confirmation before enabling writes",
        "RECOVERABLE_CONFIRMATION": "Confirm before enabling writes",
        "HARD_BLOCK": "This action is blocked",
        "AVAILABLE": None,
    }
    return mapping.get(classification)


def _print_status_rows(host, *, availability_classification: str) -> None:
    from mercury.storage.host_maintenance import writes_allowed as writes_are_allowed

    writes_on = writes_are_allowed(host)
    if host.storage_availability == "detached":
        hdd = "Not connected"
    elif writes_on:
        hdd = "Connected · mounted · writes enabled"
    else:
        hdd = "Connected · mounted"
    write_state = (
        "Enabled · primary"
        if writes_on and host.active_write_role == "primary"
        else "Disabled · disconnect preparation"
        if host.storage_availability == "detaching" or host.source_detach_preparation
        else ("Enabled" if writes_on else "Disabled")
    )

    recovery = (
        "Newer recovery artifacts exist"
        if getattr(host, "recovery_artifacts_created_after_package", False)
        or host.source_changed_since_package
        else "No newer recovery artifacts"
    )
    source_data = (
        "Known changes after package"
        if getattr(host, "source_data_changed_since_package", False)
        else "No known changes after package"
    )
    rows = [
        dashboard_row("Mercury HDD", hdd, label_width=16),
        dashboard_row("Backup writer", write_state, label_width=16),
        dashboard_row("Package", _package_status_label(host), label_width=16),
    ]
    snapshot = _package_snapshot_label(host)
    if snapshot:
        rows.append(dashboard_row("Snapshot", snapshot, label_width=16))
    rows.extend(
        [
            dashboard_row("Recovery", recovery, label_width=16),
            dashboard_row("Source data", source_data, label_width=16),
        ]
    )
    action_required = _operator_action_required(availability_classification)
    if action_required:
        rows.append(dashboard_row("Action required", action_required, label_width=16))
    for line in rows:
        output.write(line)


def _print_important_warning() -> None:
    from mercury.terminal.theme import important_banner

    output.write("")
    for line in important_banner("IMPORTANT"):
        output.write(line)
    output.write("A verified destination package already exists.")
    output.write("")
    output.write(
        "Enabling writes will allow Mercury to create newer backups and Git captures."
    )
    output.write(
        "Those new artifacts will not be included in the existing package."
    )
    output.write("")
    output.write("The existing package will remain valid for rehearsal.")


def _print_planned_session() -> None:
    output.write("")
    if colors_enabled():
        output.write(section_title("PLANNED SESSION"))
    else:
        output.write("Planned session")
    output.write(rule_line())
    output.write(
        dashboard_row("Production databases", "Back up and verify", label_width=26)
    )
    output.write(
        dashboard_row("Development databases", "Ask before running", label_width=26)
    )
    output.write(
        dashboard_row("Offline Git recovery", "Capture and verify", label_width=26)
    )
    output.write(
        dashboard_row("Production → development", "Ask before running", label_width=26)
    )


def _print_overview(*, availability_classification: str) -> None:
    host = load_host_maintenance()
    title = (
        "BACK UP AND SYNC AGAIN"
        if _package_verified(host)
        else "BACK UP AND SYNC THIS WORKSTATION"
    )
    display_screen.open_screen(title)
    _print_status_rows(host, availability_classification=availability_classification)
    if _package_verified(host):
        _print_important_warning()
    _print_planned_session()


def _choice_menu() -> str | None:
    output.write("")
    output.write(menu_item_line("1", "Restore source writer and continue", indent=2))
    output.write(menu_item_line("2", "Review or customize this session", indent=2))
    output.write(menu_item_line("0", "Cancel", indent=2))
    output.write("")
    return (menu_prompts.ask("Choice") or "").strip() or None


def _customize_plan(plan: SessionPlan) -> tuple[SessionPlan, str] | None:
    """Return ``(plan, exit_mode)`` or ``None`` on cancel.

    ``exit_mode``:
      - ``custom`` — Run selected session (honor plan exactly; no optional re-prompt)
      - ``databases_only`` — Databases only preset (may ask optional lanes)
      - ``git_only`` — Git recovery only preset
    """
    current = plan.model_copy(deep=True)
    while True:
        display_screen.open_screen("Customize Backup and Sync")
        flags = [
            ("Back up production databases", current.production_backup),
            ("Back up development databases", current.development_backup),
            ("Capture offline Git recovery", current.git_recovery),
            ("Sync production databases to development", current.sync_development),
            ("Restore-check newly written backups", current.restore_check),
        ]
        for label, enabled in flags:
            mark = "x" if enabled else " "
            output.write(f"  [{mark}] {label}")
        if current.production_backup:
            output.write("      · Verify newly written production backups (follows production)")
        output.write("")
        output.write("  [1] Toggle production backup")
        output.write("  [2] Toggle development backup")
        output.write("  [3] Toggle Git recovery")
        output.write("  [4] Toggle production-to-development sync")
        output.write("  [5] Toggle restore-check")
        output.write("  [6] Run selected session")
        output.write("  [7] Databases only")
        output.write("  [8] Git recovery only")
        output.write("  [0] Cancel")
        choice = (menu_prompts.ask("Choice") or "").strip()
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
            return current.normalize(), "custom"
        elif choice == "7":
            return (
                SessionPlan(
                    production_backup=True,
                    verify_production=True,
                    development_backup=False,
                    git_recovery=False,
                    sync_development=False,
                ).normalize(),
                "databases_only",
            )
        elif choice == "8":
            return (
                SessionPlan(
                    production_backup=False,
                    verify_production=False,
                    development_backup=False,
                    git_recovery=True,
                    sync_development=False,
                ).normalize(),
                "git_only",
            )
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
    options: list[tuple[str, str, str]] = []
    if allow_disconnect:
        options.append(("safe_disconnect", "Safely disconnect Mercury HDD", "safe_disconnect"))
    options.append(("review", "Review session details", "review"))
    options.append(("main_menu", "Return to main menu", "main_menu"))
    for index, (_key, label, _action) in enumerate(options, start=1):
        output.write(f"  [{index}] {label}")
    output.write("  [0] Exit Mercury")
    choice = (menu_prompts.ask("Choice") or "").strip()
    if choice == "0":
        return "exit"
    for index, (_key, _label, action) in enumerate(options, start=1):
        if choice == str(index):
            return action
    return None


def _ensure_writer_ready(*, hooks: SessionHooks | None) -> bool:
    """Return True when backup writes are available after optional guided restore.

    Do not pre-print strong/recoverable prompts here — ``ensure_backup_writes_available``
    (or an injected hook) owns that presentation so operators see it once.
    """
    availability = assess_operation_availability("database_backup")
    if availability.is_hard_block:
        output.write("")
        output.write(format_hard_block_message(availability))
        return False
    if availability.available:
        return True
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
    """Interactive Backup and Sync entry point.

    Returns ``None`` when the operator cancels (caller may re-show intent).
    """
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
    customize_mode: str | None = None
    if choice == "2":
        customized = _customize_plan(plan)
        if customized is None:
            display_screen.write_summary("Backup and Sync cancelled.")
            return None
        plan, customize_mode = customized
    elif choice != "1":
        output.write(menu_prompts.invalid_choice_message(choice))
        return None

    # Storage first — never ask optional lanes while writer is blocked.
    if not _ensure_writer_ready(hooks=hooks):
        display_screen.write_summary("Backup and Sync cancelled.")
        display_screen.write_summary("Mercury writes remain disabled.")
        return None

    # Optional lanes only for the recommended continue path or Databases-only preset.
    # A manually customized plan (Run selected session) must not be re-prompted.
    if choice == "1" or customize_mode == "databases_only":
        plan = _ask_optional_lanes(plan)
        if customize_mode == "databases_only":
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
