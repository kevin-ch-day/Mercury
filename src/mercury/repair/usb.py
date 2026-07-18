"""One-shot Mercury USB mount and ownership repair."""

from __future__ import annotations

from pathlib import Path

from mercury.core.paths import REPO_ROOT

USB_REPAIR_COMMAND = "./run.sh repair-usb"


def usb_repair_script_path() -> Path:
    return (REPO_ROOT / "scripts" / "repair-mercury-usb.sh").resolve()


def describe_usb_repair() -> list[str]:
    return [
        "Repairs the transitional / legacy Mercury USB volume (MERCURY_DATA_USB).",
        "Mounts the drive at the configured Linux path (default /mnt/MERCURY_DATA_USB).",
        "Creates mercury_backups/, mercury_logs/, and related layout directories.",
        "Fixes root-owned folders so Mercury can write logs and backups.",
        "Enables the systemd mount unit so the drive comes up after reboot.",
        "Does not configure or migrate the primary HDD (MERCURY_DATA_V2) — see ./run.sh storage status.",
        f"Run when the dashboard shows storage mount or permission warnings: {USB_REPAIR_COMMAND}",
    ]
