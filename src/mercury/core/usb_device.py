"""Detect Mercury USB block devices and suggest mount repair commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mercury.core.platform import detect_platform
from mercury.core.usb_mount import DEFAULT_USB_MOUNT, resolve_usb_mount, usb_mount_is_active
from mercury.repair.usb import USB_REPAIR_COMMAND

MERCURY_USB_LABEL = "MERCURY_DATA_USB"
LABEL_DEVICE = Path("/dev/disk/by-label") / MERCURY_USB_LABEL


@dataclass(frozen=True)
class UsbDeviceProbe:
    mount_path: Path
    device_attached: bool
    device_path: Path | None
    systemd_mount_unit: str | None
    fstab_configured: bool
    placeholder_mount_point: bool
    quick_mount_command: str | None


def systemd_mount_unit_name(mount_path: Path) -> str:
    """Map a mount path to its typical generated systemd unit name."""
    relative = str(mount_path.resolve()).lstrip("/").replace("/", "-")
    return f"{relative}.mount"


def _read_proc_mounts() -> list[tuple[Path, Path]]:
    mounts: list[tuple[Path, Path]] = []
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        return mounts
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mounts.append((Path(parts[0]), Path(parts[1])))
    return mounts


def _mount_path_is_active(mount_path: Path) -> bool:
    resolved = mount_path.resolve()
    for _device, target in _read_proc_mounts():
        if target.resolve() == resolved:
            return True
    try:
        return mount_path.is_mount()
    except OSError:
        return False


def _fstab_mentions_mount(mount_path: Path) -> bool:
    try:
        text = Path("/etc/fstab").read_text(encoding="utf-8")
    except OSError:
        return False
    target = str(mount_path.resolve())
    return target in text or MERCURY_USB_LABEL in text


def _resolve_label_device() -> Path | None:
    try:
        if LABEL_DEVICE.exists():
            return LABEL_DEVICE.resolve()
    except OSError:
        return None
    return None


def probe_usb_device(*, mount_path: Path | None = None) -> UsbDeviceProbe:
    """Inspect whether the Mercury USB drive is attached but unmounted."""
    resolved_mount = (mount_path or resolve_usb_mount()).resolve()
    platform = detect_platform()
    if platform.is_windows:
        return UsbDeviceProbe(
            mount_path=resolved_mount,
            device_attached=False,
            device_path=None,
            systemd_mount_unit=None,
            fstab_configured=False,
            placeholder_mount_point=False,
            quick_mount_command=None,
        )

    device_path = _resolve_label_device()
    mounted = _mount_path_is_active(resolved_mount)
    placeholder = resolved_mount.exists() and not mounted
    unit = systemd_mount_unit_name(resolved_mount)
    fstab = _fstab_mentions_mount(resolved_mount)
    device_attached = device_path is not None

    quick_command: str | None = None
    if device_attached and not mounted:
        if fstab:
            quick_command = f"sudo systemctl start {unit}"
        else:
            quick_command = f"sudo mount LABEL={MERCURY_USB_LABEL} {resolved_mount}"

    return UsbDeviceProbe(
        mount_path=resolved_mount,
        device_attached=device_attached,
        device_path=device_path,
        systemd_mount_unit=unit if fstab else None,
        fstab_configured=fstab,
        placeholder_mount_point=placeholder,
        quick_mount_command=quick_command,
    )


def operator_usb_repair_hint(*, mount_path: Path | None = None) -> str:
    """Return the best single operator command to fix an unmounted Mercury USB."""
    return USB_REPAIR_COMMAND


def log_directory_repair_hint(
    log_dir: Path,
    *,
    permission_detail: str | None = None,
) -> str:
    """Suggest mount or ownership repair when operator-storage logging is unavailable."""
    from mercury.core.path_permissions import chown_repair_command

    resolved = log_dir.expanduser().resolve()
    mount = resolve_usb_mount()
    try:
        resolved.relative_to(mount.resolve())
    except ValueError:
        if permission_detail and "owner:" in permission_detail and resolved.exists():
            return chown_repair_command(resolved)
        if resolved.exists():
            return chown_repair_command(resolved)
        return USB_REPAIR_COMMAND

    if not usb_mount_is_active(mount):
        return USB_REPAIR_COMMAND
    if permission_detail and "owner:" in permission_detail:
        return USB_REPAIR_COMMAND
    if resolved.exists():
        return USB_REPAIR_COMMAND
    return USB_REPAIR_COMMAND


def usb_repair_banner(probe: UsbDeviceProbe) -> str | None:
    """One-line operator hint when the USB drive needs mounting."""
    if _mount_path_is_active(probe.mount_path):
        return None
    if probe.device_attached or probe.placeholder_mount_point:
        return (
            f"Mercury USB is not ready at {probe.mount_path}. "
            f"Run: {USB_REPAIR_COMMAND}"
        )
    if probe.mount_path == DEFAULT_USB_MOUNT.resolve():
        return (
            f"Mercury USB not mounted at {probe.mount_path}. "
            f"Connect the drive, then run: {USB_REPAIR_COMMAND}"
        )
    return None

