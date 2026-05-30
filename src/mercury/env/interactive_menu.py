"""Interactive environment check menu (option 1)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.core.execution_policy import ENV_DRY_RUN, ENV_LIVE_ACTIONS, load_execution_policy
from mercury.database import (
    MariaDbConfigError,
    MariaDbDriverMissingError,
    MariaDbLiveError,
    probe_mariadb_server,
    try_load_mariadb_config,
)
from mercury.env.terminal.check import print_environment_check
from mercury.env.probe import probe_environment
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu

ENV_SCREEN_TITLE = "Environment Check"


def read_env_choice() -> str | None:
    return read_submenu_choice()


def _probe_database():
    from mercury.logging.events import log_mariadb_probe

    if try_load_mariadb_config() is None:
        return None, None
    try:
        probe = probe_mariadb_server()
        log_mariadb_probe(
            connected=probe.connected,
            latency_ms=probe.latency_ms,
            database_count=probe.user_database_count,
            error=probe.error,
        )
        return probe, None
    except (MariaDbConfigError, MariaDbDriverMissingError, MariaDbLiveError) as exc:
        log_mariadb_probe(connected=False, error=str(exc))
        return None, str(exc)


def _render_env_screen(*, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(ENV_SCREEN_TITLE)
    env = probe_environment(menu=True)
    probe, error = _probe_database()
    print_environment_check(env, probe, error=error)
    display_screen.write_blank()
    render_submenu(
        [
            ("1", "Rescan"),
            ("2", "Live mode guide"),
        ]
    )


def _print_live_mode_guide() -> None:
    policy = load_execution_policy()
    config_path = policy.config_path or "config/local.toml"
    display_screen.write_fields(
        {
            "current_mode": "live" if policy.live_execution_allowed() else "dry-run",
            "dry_run": policy.dry_run,
            "live_actions": policy.live_actions_enabled,
            "config_file": config_path,
        }
    )
    display_screen.write_blank()
    display_screen.write_summary("To enable backup, verify, sync, and restore execution:")
    display_screen.write_bullets(
        [
            f"Edit {config_path} [mercury]: dry_run = false, live_actions_enabled = true",
            f"Or export {ENV_DRY_RUN}=0 and {ENV_LIVE_ACTIONS}=1 for this shell only",
            "Re-run Environment Check to confirm execution shows live actions enabled",
        ]
    )


def run_env_menu(*, interactive: bool = True) -> None:
    show_title = False
    while True:
        _render_env_screen(show_title=show_title)
        show_title = False
        if not interactive:
            return

        choice = read_env_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            display_screen.write_summary("Rescanned environment.")
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            _print_live_mode_guide()
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
