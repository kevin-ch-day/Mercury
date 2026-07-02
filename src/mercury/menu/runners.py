"""Interactive menu shell."""

from __future__ import annotations

from mercury.menu import main_display as menu_display

# Re-export for tests and backward compatibility.
MENU_TITLE = menu_display.MENU_TITLE
MENU_SUBTITLE = menu_display.MENU_SUBTITLE
MENU_FOOTER = menu_display.MENU_FOOTER
MENU_ITEMS = [
    (item.key, item.title) for _section, items in menu_display.MENU_SECTIONS for item in items
]


def render_menu_text() -> str:
    return menu_display.render_main_menu()


def render_status_block(*, probe_database: bool = False, compact: bool = False) -> str:
    return menu_display.status_line(probe_database=probe_database)


def run_discover_databases() -> None:
    from mercury.database.discovery_menu import run_discover_menu

    run_discover_menu()


def run_verify_plan() -> None:
    from mercury.verify.interactive_menu import run_verify_menu

    run_verify_menu()


def run_reports_and_history() -> None:
    from mercury.reporting.interactive_menu import run_reports_menu

    run_reports_menu()


def run_sync_plan() -> None:
    from mercury.sync.interactive_menu import run_sync_menu

    run_sync_menu()


def run_backup_batch_menu() -> None:
    from mercury.backup.interactive_menu import run_backup_menu

    run_backup_menu()


def run_restore_check_menu() -> None:
    from mercury.restore.interactive_menu import run_restore_menu

    run_restore_menu()


def run_environment_check() -> None:
    from mercury.env.interactive_menu import run_env_menu

    run_env_menu()


def run_live_mode_guide() -> None:
    from mercury.env.interactive_menu import run_live_mode_guide

    run_live_mode_guide()


def run_doctor_menu() -> None:
    from mercury.env.interactive_menu import run_doctor_menu as _run_doctor_menu

    _run_doctor_menu()


def run_deploy_menu() -> None:
    from mercury.deploy.interactive_menu import run_deploy_menu as _run_deploy_menu

    _run_deploy_menu()


def run_recovery_menu() -> None:
    from mercury.recovery.interactive_menu import run_recovery_menu as _run_recovery_menu

    _run_recovery_menu()


def run_handoff_menu() -> None:
    from mercury.handoff.interactive_menu import run_handoff_menu as _run_handoff_menu

    _run_handoff_menu()


# Re-export interactive loop (implementation in menu.loop).
from mercury.menu.loop import MenuResult, handle_menu_choice, run_menu  # noqa: E402

__all__ = [
    "MenuResult",
    "MENU_FOOTER",
    "MENU_ITEMS",
    "MENU_SUBTITLE",
    "MENU_TITLE",
    "handle_menu_choice",
    "render_menu_text",
    "render_status_block",
    "run_menu",
]
