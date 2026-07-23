"""Startup intent chooser when Mercury writes are disabled (Phase 3)."""

from __future__ import annotations

from mercury import output
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen


INTENT_BACKUP_SYNC = "backup_sync"
INTENT_SAFE_DISCONNECT = "safe_disconnect"
INTENT_DESTINATION_REHEARSAL = "destination_rehearsal"
INTENT_BROWSE = "browse"
INTENT_EXIT = "exit"


def should_offer_startup_intent(*, host=None) -> bool:
    from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed

    state = host or load_host_maintenance()
    if writes_allowed(state):
        return False
    if state.storage_availability == "detached":
        return False
    return bool(
        state.source_detach_preparation
        or state.storage_availability == "detaching"
        or state.destination_rehearsal_active
        or state.destination_rehearsal_in_progress
    )


def run_startup_intent_chooser() -> str:
    """Ask what the operator wants to do; do not restore writes or detach."""
    display_screen.open_screen("What would you like to do?")
    output.write("  [1] Back up and sync this workstation")
    output.write("  [2] Safely disconnect the Mercury HDD")
    output.write("  [3] Continue destination rehearsal")
    output.write("  [4] Browse all operations")
    output.write("  [0] Exit")
    output.write("")
    choice = (menu_prompts.ask("Choice") or "").strip()
    mapping = {
        "1": INTENT_BACKUP_SYNC,
        "2": INTENT_SAFE_DISCONNECT,
        "3": INTENT_DESTINATION_REHEARSAL,
        "4": INTENT_BROWSE,
        "0": INTENT_EXIT,
    }
    return mapping.get(choice, INTENT_BROWSE)


def dispatch_startup_intent(intent: str) -> str | None:
    """Run the selected intent. Returns ``exit`` when Mercury should quit."""
    if intent == INTENT_EXIT:
        return "exit"
    if intent == INTENT_BACKUP_SYNC:
        from mercury.backup.session_wizard import run_backup_sync_wizard

        run_backup_sync_wizard()
        return None
    if intent == INTENT_SAFE_DISCONNECT:
        from mercury.storage.interactive_menu import run_safe_disconnect_wizard

        run_safe_disconnect_wizard()
        return None
    if intent == INTENT_DESTINATION_REHEARSAL:
        from mercury.menu.options import MAIN_MIGRATION, main_menu_hint
        from mercury.menu.task_menus import run_migration_hub

        display_screen.write_summary(
            f"Opening workstation migration ({main_menu_hint(MAIN_MIGRATION)})."
        )
        run_migration_hub()
        return None
    return None
