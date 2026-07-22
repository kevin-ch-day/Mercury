"""Safe Mercury HDD detach status/preview (execute stops before privileged unmount)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import subprocess

from mercury.core.storage_roles import DEFAULT_PRIMARY_UUID
from mercury.core.usb_mount import resolve_operator_mount
from mercury.migration.destination_package_create import packages_root
from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    load_host_maintenance,
    mark_detaching,
)

DETACH_CONFIRMATION = "DETACH MERCURY HDD"


@dataclass
class DetachStatus:
    expected_device: str = "/dev/sda"
    expected_partition: str = "/dev/sda1"
    expected_uuid: str = DEFAULT_PRIMARY_UUID
    mount_path: str = "/mnt/MERCURY_DATA_V2"
    mounted: bool = False
    mount_source: str = ""
    mount_uuid: str = ""
    mercury_menu_pids: list[int] = field(default_factory=list)
    open_handle_samples: list[str] = field(default_factory=list)
    active_ops: list[str] = field(default_factory=list)
    package_id: str = ""
    package_verification_status: str = ""
    package_verified: bool = False
    writer_state: str = ""
    fstab_configured: bool = False
    systemd_mount_active: bool = False
    host_maintenance: HostMaintenanceState | None = None
    safe_to_proceed: bool = False
    blockers: list[str] = field(default_factory=list)
    operator_commands: list[str] = field(default_factory=list)


def _menu_pids() -> list[int]:
    found: list[int] = []
    for pid_dir in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except OSError:
            continue
        if re.search(r"\bmercury(\.cli)?\s+menu\b", cmd) or cmd.rstrip().endswith(
            "mercury menu"
        ):
            found.append(int(pid_dir.name))
    return found


def _sample_open_handles(mount: Path, menu_pids: list[int]) -> list[str]:
    samples: list[str] = []
    for pid in menu_pids:
        fd_dir = Path(f"/proc/{pid}/fd")
        if not fd_dir.is_dir():
            continue
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith(str(mount)):
                samples.append(f"pid={pid} {target}")
    # CWD holders
    for pid_dir in Path("/proc").glob("[0-9]*"):
        try:
            cwd = os.readlink(pid_dir / "cwd")
        except OSError:
            continue
        if cwd == str(mount) or cwd.startswith(str(mount) + "/"):
            samples.append(f"pid={pid_dir.name} cwd={cwd}")
    return samples[:50]


def _latest_verified_package(mount: Path) -> tuple[str, str]:
    root = packages_root(mount)
    if not root.is_dir():
        return "", ""
    candidates = sorted(
        [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.name,
    )
    for path in reversed(candidates):
        receipt = path / "package_receipt.json"
        verify = path / "verification_report.json"
        if not receipt.is_file():
            continue
        try:
            data = json.loads(receipt.read_text(encoding="utf-8"))
            status = str(data.get("verification_status") or "")
            if status == "DESTINATION_PACKAGE_VERIFIED" and verify.is_file():
                return path.name, status
        except (OSError, json.JSONDecodeError):
            continue
    return "", ""


def build_detach_status(
    *,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    mount: Path | None = None,
) -> DetachStatus:
    mount = mount or resolve_operator_mount()
    status = DetachStatus(mount_path=str(mount), expected_uuid=expected_uuid)
    status.host_maintenance = load_host_maintenance()
    status.writer_state = (
        f"availability={status.host_maintenance.storage_availability} "
        f"writes_allowed={status.host_maintenance.writes_allowed} "
        f"role={status.host_maintenance.active_write_role}"
    )
    try:
        out = subprocess.check_output(
            ["findmnt", "-no", "SOURCE,UUID,TARGET", "-T", str(mount)],
            text=True,
        ).strip()
        if out:
            parts = out.split()
            status.mounted = True
            status.mount_source = parts[0] if parts else ""
            status.mount_uuid = parts[1] if len(parts) > 1 else ""
    except (OSError, subprocess.CalledProcessError):
        status.mounted = False

    status.mercury_menu_pids = _menu_pids()
    status.open_handle_samples = _sample_open_handles(mount, status.mercury_menu_pids)
    status.package_id, status.package_verification_status = _latest_verified_package(mount)
    status.package_verified = (
        status.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
    )

    try:
        fstab = Path("/etc/fstab").read_text(encoding="utf-8", errors="replace")
        status.fstab_configured = expected_uuid in fstab
    except OSError:
        status.fstab_configured = False

    try:
        unit = subprocess.check_output(
            ["systemctl", "is-active", "mnt-MERCURY_DATA_V2.mount"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status.systemd_mount_active = unit == "active"
    except (OSError, subprocess.CalledProcessError):
        status.systemd_mount_active = False

    if not status.package_verified:
        status.blockers.append("verified destination package required before detach")
    if status.mercury_menu_pids:
        status.blockers.append(
            f"mercury menu still running (pids {status.mercury_menu_pids}); choose [0] Exit"
        )
    if status.open_handle_samples:
        status.blockers.append("open handles under Mercury mount remain")
    if status.mounted and status.mount_uuid and status.mount_uuid != expected_uuid:
        status.blockers.append("mounted UUID does not match expected Mercury HDD")
    if not status.mounted:
        status.blockers.append("Mercury HDD is not mounted (already detached?)")

    status.safe_to_proceed = not status.blockers and status.package_verified
    status.operator_commands = [
        "cd ~",
        "sudo fuser -vm /mnt/MERCURY_DATA_V2",
        "sudo lsof +D /mnt/MERCURY_DATA_V2",
        "sync",
        "sudo sync -f /mnt/MERCURY_DATA_V2",
        "sudo systemctl stop mnt-MERCURY_DATA_V2.mount",
        "findmnt -T /mnt/MERCURY_DATA_V2",
        "mountpoint /mnt/MERCURY_DATA_V2",
        "lsblk -f",
        "udisksctl power-off -b /dev/sda",
        "# After return of the HDD: sudo systemctl unmask mnt-MERCURY_DATA_V2.mount && sudo systemctl start mnt-MERCURY_DATA_V2.mount",
    ]
    return status


def detach_preview(status: DetachStatus | None = None) -> list[str]:
    status = status or build_detach_status()
    return [
        "1. Mark Mercury write-disabled (host_maintenance: detaching)",
        "2. Refuse future HDD-backed operations",
        "3. Require mercury menu exit (operator: choose [0] Exit)",
        "4. Require no open writable handles (operator: sudo fuser/lsof)",
        "5. Run filesystem flush (sync / sync -f)",
        "6. Stop systemd mount: systemctl stop mnt-MERCURY_DATA_V2.mount",
        "7. Confirm unmounted state (findmnt/mountpoint/lsblk)",
        "8. Optionally power off /dev/sda via udisksctl",
        "9. Confirm source host remains write-disabled while disk is absent",
        "10. Optionally: systemctl mask mnt-MERCURY_DATA_V2.mount (reversible; unmask on return)",
        f"Package verified: {status.package_verified} ({status.package_id or 'none'})",
        f"Safe to proceed (pre-sudo): {status.safe_to_proceed}",
    ]


def detach_execute_until_privileged(
    *,
    confirm: str,
    mask_systemd_unit: bool = False,
) -> tuple[DetachStatus, list[str]]:
    """Apply host write-disable and return operator privileged commands. Does not unmount."""
    if confirm != DETACH_CONFIRMATION:
        status = build_detach_status()
        status.blockers.append(f"confirmation must be exactly {DETACH_CONFIRMATION!r}")
        status.safe_to_proceed = False
        return status, []

    status = build_detach_status()
    if not status.safe_to_proceed:
        return status, []

    mark_detaching(
        package_id=status.package_id,
        package_verification_status=status.package_verification_status,
    )
    status = build_detach_status()
    commands = list(status.operator_commands)
    if mask_systemd_unit:
        commands.insert(
            -1,
            "sudo systemctl mask mnt-MERCURY_DATA_V2.mount  # reversible; unmask when HDD returns",
        )
    note = (
        "STOP: privileged unmount/power-off must be run by the operator. "
        f"Cursor will not execute sudo. Generated at "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}."
    )
    return status, [note, *commands]
