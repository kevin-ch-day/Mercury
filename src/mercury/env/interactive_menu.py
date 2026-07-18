"""Interactive environment check menu (option 1)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.core.execution_policy import (
    ENV_LIVE_ACTIONS,
    backup_mode_label,
    destructive_ops_label,
    load_execution_policy,
)
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
    options: list[tuple[str, str]] = [("1", "Rescan")]
    from mercury.repair.startup import usb_repair_needed

    if usb_repair_needed():
        options.append(("2", "Repair USB mount and permissions"))
    output.write(
        menu_display.render_option_menu(
            title="Actions",
            options=options,
            bottom_label="Back",
        )
    )


def _print_live_mode_guide() -> None:
    policy = load_execution_policy()
    config_path = policy.config_path or "config/local.toml"
    display_screen.write_report_header("OPERATOR SAFETY GUIDE")
    output.write("Current state")
    output.write(_guide_field("Backup mode", backup_mode_label(policy)))
    output.write(_guide_field("Sync/deploy/restore", destructive_ops_label(policy)))
    output.write(_guide_field("Config file", config_path))
    output.write("")
    output.write("Backups")
    output.write("Backups write to operator storage when MariaDB, config, and the backup root are valid.")
    output.write("Inspect primary vs legacy roots with: ./run.sh storage status")
    output.write("Use Backup Operations -> Run full backup now, or: ./run.sh backup all")
    output.write("Use Preview backup plan or ./run.sh backup plan --dry-run to preview only.")
    output.write("")
    output.write("Verification")
    output.write("Verification is safe and updates manifests/ledger when checks pass.")
    output.write("Use Backup Operations -> Verify source backups, or: ./run.sh backup verify-all")
    output.write("")
    output.write("Destructive actions")
    output.write("Prod-to-dev sync, deploy, and restore still require live_actions_enabled.")
    output.write(f"Edit {config_path}:")
    output.write("live_actions_enabled = true")
    output.write("dry_run = false  # only needed for sync/deploy/restore, not backups")
    output.write("")
    output.write("Or for this shell only:")
    output.write(f"export {ENV_LIVE_ACTIONS}=1")
    output.write("")
    output.write("Safety")
    output.write("Sync requires typing SYNC DEV and readiness checks.")
    output.write("Production restore is never allowed.")
    output.write("Missing protected sources are refused, not silently skipped as success.")


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
            from mercury.repair.startup import run_usb_repair_flow

            run_usb_repair_flow(interactive=True, default_yes=True)
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))


def run_live_mode_guide() -> None:
    _print_live_mode_guide()
    menu_prompts.wait_for_continue()


def run_doctor_menu(*, interactive: bool = True) -> None:
    from mercury.env.doctor import run_doctor
    from mercury.env.terminal.doctor import print_doctor_report, print_repair_plan
    from mercury.menu import prompts as menu_prompts

    show_title = True
    while True:
        if show_title:
            display_screen.write_report_header("SYSTEM DOCTOR")
        report = run_doctor(probe_database=True)
        print_doctor_report(report)
        display_screen.write_blank()
        options: list[tuple[str, str]] = [
            ("1", "Show repair plan"),
            ("2", "Rescan"),
            ("3", "Open storage migration menu"),
        ]
        from mercury.repair.startup import usb_repair_needed

        if usb_repair_needed():
            options.append(("4", "Repair USB mount and permissions"))
        output.write(
            menu_display.render_option_menu(
                title="Actions",
                options=options,
                bottom_label="Back",
            )
        )
        if not interactive:
            return
        choice = read_submenu_choice()
        if choice is None or choice == "0":
            return
        if choice == "1":
            print_repair_plan(report)
            menu_prompts.wait_for_continue()
            show_title = False
            continue
        if choice == "2":
            display_screen.write_summary("Rescanned environment.")
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            from mercury.storage.interactive_menu import run_storage_menu

            run_storage_menu(interactive=True)
            show_title = pause_and_redraw()
            continue
        if choice == "4":
            from mercury.repair.startup import run_usb_repair_flow

            run_usb_repair_flow(interactive=True, default_yes=True)
            show_title = pause_and_redraw()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))
