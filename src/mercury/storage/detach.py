"""Safe Mercury HDD detach status/preview and CLI-facing helpers.

Device identity is always resolved by filesystem UUID — never by a fixed
``/dev/sdX`` letter. Prefer ``detach_wizard.run_detach_wizard`` for execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mercury.core.storage_roles import DEFAULT_PRIMARY_MOUNT, DEFAULT_PRIMARY_UUID
from mercury.storage.block_device import resolve_mercury_block_device
from mercury.storage.detach_wizard import (
    latest_verified_package,
    run_detach_preflight,
)
from mercury.storage.host_maintenance import load_host_maintenance

__all__ = [
    "DetachStatus",
    "build_detach_status",
    "detach_preview",
]


@dataclass
class DetachStatus:
    expected_uuid: str = DEFAULT_PRIMARY_UUID
    mount_path: str = DEFAULT_PRIMARY_MOUNT
    partition_device: str = ""
    parent_device: str = ""
    model: str = ""
    label: str = ""
    mounted: bool = False
    mount_uuid: str = ""
    mercury_menu_pids: list[int] = field(default_factory=list)
    open_handle_samples: list[str] = field(default_factory=list)
    package_id: str = ""
    package_verification_status: str = ""
    package_verified: bool = False
    writer_state: str = ""
    fstab_configured: bool = False
    systemd_mount_active: bool = False
    result_hint: str = ""
    safe_to_proceed: bool = False
    blockers: list[str] = field(default_factory=list)


def build_detach_status(
    *,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    mount: Path | None = None,
) -> DetachStatus:
    mount = mount or Path(DEFAULT_PRIMARY_MOUNT)
    status = DetachStatus(mount_path=str(mount), expected_uuid=expected_uuid)
    host = load_host_maintenance()
    status.writer_state = (
        f"availability={host.storage_availability} "
        f"writes_allowed={host.writes_allowed} "
        f"role={host.active_write_role}"
    )
    resolved = resolve_mercury_block_device(
        expected_uuid=expected_uuid,
        expected_mount=str(mount),
        require_mounted=False,
    )
    if resolved.identity:
        ident = resolved.identity
        status.partition_device = ident.partition_device
        status.parent_device = ident.parent_device
        status.model = ident.model
        status.label = ident.label
        status.mounted = bool(ident.mountpoint)
        status.mount_uuid = ident.uuid
    else:
        status.blockers.extend(resolved.errors)

    pre = run_detach_preflight(
        mount=mount,
        expected_uuid=expected_uuid,
        skip_log_redirect=True,
        mutate_host=False,
    )
    status.package_id = pre.package_id
    status.package_verification_status = (
        "DESTINATION_PACKAGE_VERIFIED" if pre.package_id else ""
    )
    if not status.package_id:
        pkg_id, pkg_status = latest_verified_package(mount)
        status.package_id = pkg_id
        status.package_verification_status = pkg_status
    status.package_verified = (
        status.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
    )
    status.result_hint = pre.result_state
    status.blockers.extend([b for b in pre.blockers if b not in status.blockers])
    status.safe_to_proceed = pre.ok and pre.result_state == "PREFLIGHT_OK"

    try:
        fstab = Path("/etc/fstab").read_text(encoding="utf-8", errors="replace")
        status.fstab_configured = expected_uuid in fstab
    except OSError:
        status.fstab_configured = False

    try:
        import subprocess

        from mercury.storage.block_device import systemd_mount_unit_for_path

        unit = systemd_mount_unit_for_path(mount)
        out = subprocess.check_output(
            ["systemctl", "is-active", unit],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status.systemd_mount_active = out == "active"
    except (OSError, subprocess.CalledProcessError):
        status.systemd_mount_active = False

    return status


def detach_preview(status: DetachStatus | None = None) -> list[str]:
    status = status or build_detach_status()
    return [
        "1. Resolve Mercury HDD by filesystem UUID (never a fixed /dev letter)",
        "2. Verify destination package + write-disabled host maintenance",
        "3. Redirect Mercury logs off the HDD",
        "4. Check holders (fuser/lsof) via interactive sudo",
        "5. Flush filesystem writes",
        "6. systemctl stop <mount-unit> (no lazy/forced umount)",
        "7. Confirm UUID has no mountpoint; leave MERCURY_DATA_USB alone",
        "8. udisksctl power-off -b <re-resolved parent>",
        f"Package verified: {status.package_verified} ({status.package_id or 'none'})",
        f"Resolved: {status.partition_device or '?'} → {status.parent_device or '?'} "
        f"({status.model or 'model?'})",
        f"Preflight: {status.result_hint or 'unknown'}",
    ]

