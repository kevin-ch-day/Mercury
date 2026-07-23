"""Startup intent chooser when Mercury writes are disabled (Phase 3)."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.menu.destination_move import destination_move_action_label
from mercury.menu.main_display import MENU_SUBTITLE
from mercury.terminal import screen as display_screen
from mercury.terminal.theme import (
    dashboard_row,
    menu_header_lines,
    menu_item_line,
    rule_line,
    section_title,
)
from mercury.terminal.theme import colors_enabled


# Symbolic intent action IDs (stable — menu numbers are derived, never hardcoded in logic).
INTENT_BACKUP_SYNC = "backup_sync"
INTENT_SAFE_DISCONNECT = "safe_disconnect"
INTENT_DESTINATION_REHEARSAL = "destination_rehearsal"
INTENT_BROWSE = "browse"
INTENT_OPTIONS = "options"
INTENT_EXIT = "exit"
INTENT_RECONNECT = "reconnect"
INTENT_VERIFY_PACKAGE = "verify_package"

# Dispatch outcomes for the menu loop.
OUTCOME_EXIT = "exit"
OUTCOME_CANCELLED = "cancelled"  # re-show intent chooser
OUTCOME_CONTINUE = None  # proceed to main menu / next screen


def should_offer_startup_intent(*, host=None) -> bool:
    """True when the operator should see the task intent chooser first.

    Driven by the recommendation service so startup gating and dashboard
    recommendations stay aligned.
    """
    from mercury.menu.recommendation import build_main_menu_recommendation
    from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed

    state = host or load_host_maintenance()
    if writes_allowed(state):
        return False
    if state.storage_availability == "detached":
        return False
    return bool(build_main_menu_recommendation(host=state).intent_chooser_required)


def _package_verified(host) -> bool:
    return host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"


def _no_post_package_changes(host) -> bool:
    return not (
        getattr(host, "recovery_artifacts_created_after_package", False)
        or host.source_changed_since_package
        or getattr(host, "source_data_changed_since_package", False)
    )


def _package_label(host) -> str:
    if not _package_verified(host):
        return "Not verified"
    return "VERIFIED · ready for destination rehearsal"


def _source_state_label(host) -> str:
    if _no_post_package_changes(host):
        return "No changes since package"
    parts: list[str] = []
    if getattr(host, "recovery_artifacts_created_after_package", False) or host.source_changed_since_package:
        parts.append("newer recovery artifacts")
    if getattr(host, "source_data_changed_since_package", False):
        parts.append("source-data changes")
    return "Has " + " and ".join(parts) if parts else "Unknown"


def build_startup_intent_options(*, host=None) -> list[tuple[str, str, str]]:
    """Return ordered ``(key, label, action_id)`` for the startup intent screen.

    Ordering is state-driven. Keys are assigned after ordering so business logic
    never depends on a fixed number.
    """
    from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed

    state = host or load_host_maintenance()
    verified = _package_verified(state)
    writes = writes_allowed(state)
    detached = state.storage_availability == "detached"
    rehearsal_focus = bool(
        state.destination_rehearsal_active or state.destination_rehearsal_in_progress
    ) and not (
        state.source_detach_preparation or state.storage_availability == "detaching"
    )

    # (action_id, base_label) before numbering.
    ordered: list[tuple[str, str]] = []

    if detached:
        ordered = [
            (INTENT_RECONNECT, "Reconnect or inspect Mercury HDD"),
            (INTENT_BROWSE, "Browse all operations"),
        ]
    elif not verified and (
        state.source_detach_preparation or state.storage_availability == "detaching"
    ):
        ordered = [
            (INTENT_VERIFY_PACKAGE, "Verify destination package"),
            (INTENT_BACKUP_SYNC, "Back up and sync this workstation"),
            (INTENT_BROWSE, "Browse all operations"),
        ]
    elif rehearsal_focus and verified:
        ordered = [
            (INTENT_DESTINATION_REHEARSAL, destination_move_action_label(host=state)),
            (INTENT_BACKUP_SYNC, "Back up and sync again"),
            (INTENT_SAFE_DISCONNECT, "Safely disconnect the Mercury HDD"),
            (INTENT_BROWSE, "Browse all operations"),
        ]
    elif verified and not writes:
        # Current live-like state: disconnect is the system-wide recommendation.
        ordered = [
            (INTENT_SAFE_DISCONNECT, "Safely disconnect the Mercury HDD"),
            (INTENT_BACKUP_SYNC, "Back up and sync again"),
            (INTENT_DESTINATION_REHEARSAL, destination_move_action_label(host=state)),
            (INTENT_BROWSE, "Browse all operations"),
        ]
    elif writes:
        ordered = [
            (INTENT_BACKUP_SYNC, "Back up and sync this workstation"),
            (INTENT_BROWSE, "Browse all operations"),
        ]
    else:
        ordered = [
            (INTENT_BACKUP_SYNC, "Back up and sync this workstation"),
            (INTENT_SAFE_DISCONNECT, "Safely disconnect the Mercury HDD"),
            (INTENT_DESTINATION_REHEARSAL, destination_move_action_label(host=state)),
            (INTENT_BROWSE, "Browse all operations"),
        ]

    # Options is always available (host-local appearance); never the recommendation.
    ordered.append((INTENT_OPTIONS, "Options"))

    recommended = recommended_startup_action(host=state)
    options: list[tuple[str, str, str]] = []
    for index, (action_id, label) in enumerate(ordered, start=1):
        suffix = "      recommended" if action_id == recommended else ""
        options.append((str(index), f"{label}{suffix}", action_id))
    return options


def recommended_startup_action(*, host=None) -> str:
    """Symbolic action id for the startup Recommended row."""
    from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed

    state = host or load_host_maintenance()
    if state.storage_availability == "detached":
        return INTENT_RECONNECT
    if writes_allowed(state):
        return INTENT_BACKUP_SYNC
    verified = _package_verified(state)
    if not verified and (
        state.source_detach_preparation or state.storage_availability == "detaching"
    ):
        return INTENT_VERIFY_PACKAGE
    rehearsal_only = bool(
        state.destination_rehearsal_active or state.destination_rehearsal_in_progress
    ) and not (
        state.source_detach_preparation or state.storage_availability == "detaching"
    )
    if rehearsal_only and verified:
        return INTENT_DESTINATION_REHEARSAL
    if verified and not writes_allowed(state):
        return INTENT_SAFE_DISCONNECT
    return INTENT_BACKUP_SYNC


def render_startup_intent_context(*, host=None) -> list[str]:
    """Status lines for the CURRENT SESSION panel (colon-free aligned rows)."""
    from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed

    state = host or load_host_maintenance()
    writes = writes_allowed(state)
    if state.storage_availability == "detached":
        hdd = "Not connected"
    elif writes:
        hdd = "Connected · mounted · writes enabled"
    else:
        hdd = "Connected · mounted · writes disabled"

    recommended = recommended_startup_action(host=state)
    labels = {
        INTENT_SAFE_DISCONNECT: "Safely disconnect the Mercury HDD",
        INTENT_BACKUP_SYNC: (
            "Back up and sync again"
            if _package_verified(state)
            else "Back up and sync this workstation"
        ),
        INTENT_DESTINATION_REHEARSAL: destination_move_action_label(host=state),
        INTENT_RECONNECT: "Reconnect or inspect Mercury HDD",
        INTENT_VERIFY_PACKAGE: "Verify destination package",
        INTENT_BROWSE: "Browse all operations",
    }
    lines = [
        dashboard_row("Mercury HDD", hdd, label_width=14),
        dashboard_row("Package", _package_label(state), label_width=14),
        dashboard_row("Source state", _source_state_label(state), label_width=14),
        dashboard_row("Recommended", labels.get(recommended, recommended), label_width=14),
    ]
    return lines


def run_startup_intent_chooser(*, host=None) -> str:
    """Ask what the operator wants to do; do not restore writes or detach."""
    from mercury.storage.host_maintenance import load_host_maintenance

    state = host or load_host_maintenance()
    options = build_startup_intent_options(host=state)

    for line in menu_header_lines(MENU_SUBTITLE):
        output.write(line)
    output.write("")
    if colors_enabled():
        output.write(section_title("CURRENT SESSION"))
    else:
        output.write("CURRENT SESSION")
    output.write(rule_line())
    for line in render_startup_intent_context(host=state):
        output.write(line)
    output.write("")
    for key, label, _action in options:
        output.write(menu_item_line(key, label, indent=2))
    output.write(menu_item_line("0", "Exit", indent=2))
    output.write("")
    while True:
        choice = (menu_prompts.ask("Choice") or "").strip()
        if choice == "0":
            return INTENT_EXIT
        for key, _label, action_id in options:
            if choice == key:
                return action_id
        output.write(menu_prompts.invalid_choice_message(choice))


def dispatch_startup_intent(intent: str) -> str | None:
    """Run the selected intent.

    Returns:
      ``exit`` — quit Mercury
      ``cancelled`` — re-show the intent chooser
      ``None`` — continue into the main menu
    """
    if intent == INTENT_EXIT:
        return OUTCOME_EXIT
    if intent == INTENT_BACKUP_SYNC:
        from mercury.backup.session_wizard import run_backup_sync_wizard

        session = run_backup_sync_wizard()
        if session is None:
            return OUTCOME_CANCELLED
        return OUTCOME_CONTINUE
    if intent == INTENT_SAFE_DISCONNECT:
        from mercury.storage.interactive_menu import run_safe_disconnect_wizard

        outcome = run_safe_disconnect_wizard()
        if outcome is False:
            return OUTCOME_CANCELLED
        if outcome == "exit":
            return OUTCOME_EXIT
        return OUTCOME_CONTINUE
    if intent == INTENT_DESTINATION_REHEARSAL:
        from mercury.menu.task_menus import run_destination_rehearsal_hub

        run_destination_rehearsal_hub()
        return OUTCOME_CONTINUE
    if intent == INTENT_RECONNECT:
        from mercury.storage.interactive_menu import run_storage_menu

        run_storage_menu()
        return OUTCOME_CONTINUE
    if intent == INTENT_VERIFY_PACKAGE:
        from mercury.storage.interactive_menu import run_storage_menu

        display_screen.write_summary(
            "Open Mercury HDD and Storage → Storage status and validation "
            "to review the destination package."
        )
        run_storage_menu()
        return OUTCOME_CONTINUE
    if intent == INTENT_OPTIONS:
        from mercury.menu.options_menu import run_options_menu

        run_options_menu()
        return OUTCOME_CANCELLED
    if intent == INTENT_BROWSE:
        return OUTCOME_CONTINUE
    return OUTCOME_CONTINUE
