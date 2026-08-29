"""Interactive menu shell."""

from __future__ import annotations

from mercury.menu import main_display as menu_display

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


def run_reports_and_history() -> None:
    from mercury.reporting.interactive_menu import run_reports_menu

    run_reports_menu()


def run_storage_menu() -> None:
    from mercury.storage.interactive_menu import run_storage_menu as _run_storage_menu

    _run_storage_menu()


def run_backup_hub() -> None:
    from mercury.menu.task_menus import run_backup_hub as _run

    _run()


def run_sync_hub() -> None:
    from mercury.menu.task_menus import run_sync_hub as _run

    _run()


def run_repo_hub() -> None:
    from mercury.menu.task_menus import run_repo_hub as _run

    _run()


def run_recovery_hub() -> None:
    from mercury.menu.task_menus import run_recovery_hub as _run

    _run()


def run_migration_hub() -> None:
    from mercury.menu.task_menus import run_migration_hub as _run

    _run()


def run_deploy_handoff_hub() -> None:
    from mercury.menu.task_menus import run_deploy_handoff_hub as _run

    _run()


def run_health_hub() -> None:
    from mercury.menu.task_menus import run_health_hub as _run

    _run()


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
    "run_backup_hub",
    "run_sync_hub",
    "run_repo_hub",
    "run_deploy_handoff_hub",
    "run_health_hub",
    "run_migration_hub",
    "run_recovery_hub",
    "run_storage_menu",
    "run_reports_and_history",
]
