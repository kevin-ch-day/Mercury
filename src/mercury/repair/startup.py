"""Interactive USB repair prompt at Mercury menu startup."""

from __future__ import annotations

import os
import subprocess
import sys

from mercury import output
from mercury.core.environment_status import EnvironmentStatus, build_environment_status
from mercury.core.platform import detect_platform
from mercury.repair.usb import USB_REPAIR_COMMAND, usb_repair_script_path
from mercury.terminal import screen as display_screen

_SKIP_ENV = "MERCURY_SKIP_USB_REPAIR"


def usb_repair_session_skipped() -> bool:
    return os.environ.get(_SKIP_ENV) == "1"


def skip_usb_repair_for_session() -> None:
    os.environ[_SKIP_ENV] = "1"


def usb_repair_reason(env: EnvironmentStatus) -> str | None:
    """Return a short reason when the one-shot USB repair helper should run."""
    if detect_platform().is_windows:
        return None

    if env.usb.repair_banner:
        return "Mercury USB is plugged in but not mounted or not ready"

    mount = env.usb.mount_path.resolve()
    for check in env.permission_checks:
        if not check.needs_repair:
            continue
        try:
            check.path.resolve().relative_to(mount)
        except ValueError:
            continue
        detail = check.detail or check.label
        return f"USB path not usable — {detail}"

    return None


def usb_repair_needed(*, probe_database: bool = False) -> bool:
    return usb_repair_reason(build_environment_status(probe_database=probe_database)) is not None


def apply_usb_repair() -> bool:
    """Run the sudo USB repair script. Returns True on success."""
    script = usb_repair_script_path()
    if not script.is_file():
        display_screen.write_status("fail", f"Repair script not found: {script}")
        return False

    argv = [str(script)] if os.geteuid() == 0 else ["sudo", str(script)]
    completed = subprocess.run(argv, check=False)
    return completed.returncode == 0


def _reconfigure_logging_after_repair() -> None:
    from mercury.logging.engine import configure_logging, reset_logging

    reset_logging()
    configure_logging()


def _repair_local_config_after_usb() -> list[str]:
    try:
        from mercury.config.init import repair_local_config_paths

        return repair_local_config_paths()
    except Exception:
        return []


def _report_usb_repair_outcome(*, repaired: bool) -> bool:
    if not repaired:
        display_screen.write_status(
            "fail",
            f"USB repair failed or was cancelled. Run {USB_REPAIR_COMMAND} manually, then ./run.sh doctor.",
        )
        return False

    _reconfigure_logging_after_repair()
    config_lines = _repair_local_config_after_usb()
    env = build_environment_status(probe_database=False)
    if usb_repair_reason(env) is None:
        display_screen.write_status("ok", "USB repair completed. Mercury can use the USB drive again.")
        for line in config_lines:
            output.item(line)
        return True

    display_screen.write_status(
        "warn",
        "USB repair finished, but Mercury still reports USB issues. Run ./run.sh doctor.",
    )
    return False


def _prompt_for_usb_repair(reason: str, *, default_yes: bool = True) -> bool | None:
    from mercury.menu import prompts as menu_prompts

    output.write("")
    display_screen.write_summary(reason)
    for line in (
        "Repair will mount the USB drive, create mercury_* folders if needed,",
        "fix root-owned directories, and enable boot mount when systemd is configured.",
        f"Manual command: {USB_REPAIR_COMMAND}",
    ):
        output.item(line)
    output.write("")
    return menu_prompts.ask_yes_no(
        "Run USB repair now? (you will be prompted for your sudo password)",
        default=default_yes,
    )


def run_usb_repair_flow(*, interactive: bool = True, default_yes: bool = True) -> bool:
    """
    Offer and/or run USB repair.

    Returns True when the USB is ready afterward (already OK, repaired, or user skipped
    in non-blocking mode).
    """
    if detect_platform().is_windows:
        return True

    env = build_environment_status(probe_database=False)
    reason = usb_repair_reason(env)
    if reason is None:
        return True

    if interactive:
        if usb_repair_session_skipped():
            return False
        if not sys.stdin.isatty():
            return False
        choice = _prompt_for_usb_repair(reason, default_yes=default_yes)
        if choice is None:
            display_screen.write_summary("USB repair skipped.")
            skip_usb_repair_for_session()
            return False
        if not choice:
            display_screen.write_summary(
                f"USB repair skipped — backups and logging may stay blocked. Run {USB_REPAIR_COMMAND} when ready."
            )
            skip_usb_repair_for_session()
            return False

    display_screen.write_summary("Running USB repair ...")
    return _report_usb_repair_outcome(repaired=apply_usb_repair())


def maybe_prompt_usb_repair_at_startup() -> None:
    """Before the main menu, offer USB repair when the drive is attached but unusable."""
    if usb_repair_session_skipped():
        return
    if not sys.stdin.isatty():
        return
    if detect_platform().is_windows:
        return

    run_usb_repair_flow(interactive=True, default_yes=True)


def main_menu_usb_repair_hint() -> str | None:
    """One-line hint for the main menu when USB repair is available."""
    if usb_repair_session_skipped() or not usb_repair_needed():
        return None
    return f"Quick fix: enter r to repair USB, or run {USB_REPAIR_COMMAND}"


def main_menu_invalid_choice_suffix() -> str:
    if usb_repair_needed() and not usb_repair_session_skipped():
        return ", or r to repair USB"
    return ""
