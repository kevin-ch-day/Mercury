"""Interactive menu shell."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.terminal import screen as display_screen

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
    from mercury.core.execution_policy import load_execution_policy
    from mercury.core.runtime import should_probe_database_status
    from mercury.reporting.protection import build_protection_report, print_protection_report
    from mercury.database import MariaDbConfigError, MariaDbLiveError
    from mercury.backup.on_disk_index import build_on_disk_backup_list
    from mercury.backup.terminal.verify import print_on_disk_backup_list

    policy = load_execution_policy()
    backup_list = build_on_disk_backup_list(policy.backup_root)
    print_on_disk_backup_list(backup_list, compact=True, menu=True)
    display_screen.write_blank()

    live = should_probe_database_status()
    try:
        print_protection_report(build_protection_report(live=live), compact=True)
    except (MariaDbConfigError, MariaDbLiveError) as exc:
        menu_display.write_status("warn", f"Live report unavailable: {exc}")
        display_screen.write_blank()
        print_protection_report(build_protection_report(), compact=True)
    except Exception as exc:
        menu_display.write_status("fail", f"Report failed: {exc}")
        display_screen.write_blank()
        print_protection_report(build_protection_report(), compact=True)

    from mercury.menu import prompts as menu_prompts

    menu_prompts.wait_for_continue()


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
