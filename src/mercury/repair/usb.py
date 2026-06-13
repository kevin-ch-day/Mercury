"""One-shot Mercury USB mount and ownership repair."""

from __future__ import annotations

from pathlib import Path

from mercury.core.paths import REPO_ROOT

USB_REPAIR_COMMAND = "./run.sh repair-usb"


def usb_repair_script_path() -> Path:
    return (REPO_ROOT / "scripts" / "repair-mercury-usb.sh").resolve()


def describe_usb_repair() -> list[str]:
    return [
        "Mounts the MERCURY_DATA_USB drive at the configured Linux path.",
        "Creates mercury_backups/, mercury_logs/, and related USB directories.",
        "Fixes root-owned USB folders so Mercury can write logs and backups.",
        "Enables the systemd mount unit so the drive comes up after reboot.",
        f"Run once when the dashboard shows USB mount or storage warnings: {USB_REPAIR_COMMAND}",
    ]
