"""USB mount resolution and active-state checks for Linux and Windows."""

from __future__ import annotations

import os
import string
from pathlib import Path

import tomllib

from mercury.core.paths import LOCAL_CONFIG
from mercury.core.platform import PlatformInfo, detect_platform

ENV_USB_MOUNT = "MERCURY_USB_MOUNT"
DEFAULT_USB_MOUNT = Path("/mnt/MERCURY_DATA_USB")
MERCURY_USB_MARKERS = ("mercury_backups", "mercury_logs")


def _load_mercury_section(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    section = data.get("mercury")
    if isinstance(section, dict):
        return section
    return {}


def mercury_layout_present(mount_path: Path) -> bool:
    return mount_path.exists() and all((mount_path / marker).is_dir() for marker in MERCURY_USB_MARKERS)


def _windows_auto_detect_usb_mount() -> Path | None:
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:/")
        if mercury_layout_present(root):
            return root
    return None


def resolve_usb_mount(*, local_config: Path | None = None) -> Path:
    """
    Resolve the Mercury USB root directory.

    Precedence (highest first):
    - MERCURY_USB_MOUNT environment variable
    - [mercury].usb_mount in config/local.toml
    - Windows auto-detect (drive letter with mercury_backups + mercury_logs)
    - DEFAULT_USB_MOUNT (/mnt/MERCURY_DATA_USB on Linux)
    """
    env_value = os.environ.get(ENV_USB_MOUNT)
    if env_value and str(env_value).strip():
        return Path(str(env_value).strip()).expanduser().resolve()

    config_path = local_config or LOCAL_CONFIG
    section = _load_mercury_section(config_path)
    configured = section.get("usb_mount")
    if configured and str(configured).strip():
        return Path(str(configured).strip()).expanduser().resolve()

    platform_info = detect_platform()
    if platform_info.is_windows:
        detected = _windows_auto_detect_usb_mount()
        if detected is not None:
            return detected.resolve()

    return DEFAULT_USB_MOUNT.resolve()


def usb_mount_is_active(mount_path: Path, *, platform_info: PlatformInfo | None = None) -> bool:
    """True when the USB target appears mounted and usable for Mercury."""
    if not mount_path.exists():
        return False

    info = platform_info or detect_platform()
    if info.is_windows:
        if mercury_layout_present(mount_path):
            return True
        try:
            return mount_path.is_mount()
        except OSError:
            return mount_path.is_dir()

    try:
        return mount_path.is_mount()
    except OSError:
        return False


def usb_mount_label(mount_path: Path) -> str:
    return str(mount_path)


def default_usb_path_replacements(mount_path: Path) -> dict[str, str]:
    """Standard Mercury subpaths under a USB mount root."""
    root = mount_path.resolve()
    return {
        "backup_root": str(root / "mercury_backups"),
        "log_dir": str(root / "mercury_logs"),
        "repo_backup_root": str(root / "mercury_repo_backups"),
        "manifest_dir": str(root / "mercury_manifests"),
        "runbook_dir": str(root / "mercury_runbooks"),
    }


def assert_operator_usb_path(path: Path, *, usb_mount: Path | None = None) -> None:
    """Refuse writes outside the resolved Mercury USB mount when it is not active."""
    mount = (usb_mount or resolve_usb_mount()).resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(mount)
    except ValueError as exc:
        raise ValueError(f"path is not under {mount}: {resolved}") from exc
    if not usb_mount_is_active(mount):
        raise ValueError(f"required USB mount is not active: {mount}")
