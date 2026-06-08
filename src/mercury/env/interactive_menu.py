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
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice

ENV_SCREEN_TITLE = "ENVIRONMENT CHECK"


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
        display_screen.write_report_header(ENV_SCREEN_TITLE)
    env = probe_environment(menu=True)
    probe, error = _probe_database()
    print_environment_check(env, probe, error=error)
    display_screen.write_blank()
    output.write(
        menu_display.render_option_menu(
            title="Actions",
            options=[
                ("1", "Rescan"),
                ("2", "Live mode guide"),
            ],
            bottom_label="Back",
        )
    )


def _print_live_mode_guide() -> None:
    policy = load_execution_policy()
    config_path = policy.config_path or "config/local.toml"
    display_screen.write_report_header("LIVE MODE GUIDE")
    output.write("Current state")
    output.write(_guide_field("Mode", "LIVE" if policy.live_execution_allowed() else "DRY RUN"))
    output.write(_guide_field("Live actions", "enabled" if policy.live_actions_enabled else "disabled"))
    output.write(_guide_field("Config file", config_path))
    output.write("")
    output.write("Before enabling live writes")
    output.write("Show backup plan.")
    output.write("Confirm the USB target is /mnt/MERCURY_DATA_USB/mercury_backups.")
    output.write("Confirm the three source databases are correct.")
    output.write("")
    output.write("How to enable live writes")
    output.write(f"Edit {config_path}:")
    output.write("dry_run = false")
    output.write("live_actions_enabled = true")
    output.write("")
    output.write("Or for this shell only:")
    output.write(f"export {ENV_DRY_RUN}=0")
    output.write(f"export {ENV_LIVE_ACTIONS}=1")
    output.write("")
    output.write("What live writes can do")
    output.write("Backups write artifacts to the USB target.")
    output.write("Restore-check may create temporary _restorecheck_* databases.")
    output.write("Prod-to-dev sync is separately gated and only destructive to dev targets.")
    output.write("Production restore is never allowed.")
    output.write("")
    output.write("After enabling")
    output.write("Re-run Environment Check.")
    output.write("Run one controlled backup first.")


def _guide_field(name: str, value: object, *, label_width: int = 20) -> str:
    return f"{name:<{label_width}}{value}"


def run_env_menu(*, interactive: bool = True) -> None:
    show_title = True
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
