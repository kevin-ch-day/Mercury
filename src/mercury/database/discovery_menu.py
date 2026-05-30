"""Interactive database discovery menu (option 2)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.database import (
    MariaDbConfigError,
    MariaDbDriverMissingError,
    MariaDbLiveError,
    discover,
    discover_demo,
    try_load_mariadb_config,
)
from mercury.database.core import DatabaseInventory
from mercury.database.terminal.discover_menu import print_discover_menu
from mercury.database.terminal.inspect import print_database_inspect_menu
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.stats import fetch_all_database_stats
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu

DISCOVER_SCREEN_TITLE = "Discover / Classify Databases"


def read_discover_choice() -> str | None:
    return read_submenu_choice()


def _load_inventory() -> tuple[DatabaseInventory, dict[str, int] | None, str | None]:
    """Return inventory, optional size map, and fallback note."""
    try:
        inventory = discover("live")
        size_by_name: dict[str, int] | None = None
        config = try_load_mariadb_config()
        if config is not None:
            try:
                stats = fetch_all_database_stats(config)
                size_by_name = {entry.name: entry.total_bytes for entry in stats.databases}
            except MariaDbLiveError:
                size_by_name = None
        return inventory, size_by_name, None
    except MariaDbConfigError as exc:
        from mercury.logging.events import log_inventory_fallback

        log_inventory_fallback(reason=str(exc), fallback="demo")
        return discover_demo(), None, f"Live discovery unavailable: {exc}"
    except (MariaDbDriverMissingError, MariaDbLiveError) as exc:
        from mercury.logging.events import log_database_error, log_inventory_fallback

        log_database_error(operation="discover", error=str(exc))
        log_inventory_fallback(reason=str(exc), fallback="demo")
        return discover_demo(), None, f"Live discovery failed: {exc}"


def _render_discover_screen(
    inventory: DatabaseInventory,
    *,
    size_by_name: dict[str, int] | None,
    note: str | None,
    show_title: bool,
) -> None:
    if show_title:
        menu_display.open_screen(DISCOVER_SCREEN_TITLE)
    if note:
        menu_display.write_status("warn", note)
        display_screen.write_summary("Showing demo catalog instead.")
    print_discover_menu(inventory, size_by_name=size_by_name)
    display_screen.write_blank()
    options: list[tuple[str, str]] = [("1", "Rescan inventory")]
    if try_load_mariadb_config() is not None:
        options.append(("2", "Inspect database"))
    render_submenu(options)


def _inspect_database_prompt() -> None:
    config = try_load_mariadb_config()
    if config is None:
        menu_display.write_status("warn", "MariaDB not configured — run: ./run.sh config init")
        return

    name = menu_prompts.ask_stripped("\nDatabase name to inspect: ")
    if name is None:
        display_screen.write_summary("Inspect cancelled.")
        return
    if not name:
        menu_display.write_status("warn", "No database name entered.")
        return

    try:
        result = inspect_database_on_server(name, config)
    except MariaDbLiveError as exc:
        menu_display.write_status("fail", f"Inspect failed: {exc}")
        return

    print_database_inspect_menu(result)


def run_discover_menu(*, interactive: bool = True) -> None:
    inventory, size_by_name, note = _load_inventory()
    show_title = False
    while True:
        _render_discover_screen(
            inventory,
            size_by_name=size_by_name,
            note=note,
            show_title=show_title,
        )
        note = None
        show_title = False
        if not interactive:
            return

        choice = read_discover_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            inventory, size_by_name, note = _load_inventory()
            display_screen.write_summary(
                f"Rescanned — {inventory.count} database(s), "
                f"{sum(1 for entry in inventory.entries if entry.backup_source)} backup source(s)."
            )
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            _inspect_database_prompt()
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
