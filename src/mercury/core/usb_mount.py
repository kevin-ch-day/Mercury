"""USB mount resolution and active-state checks for Linux and Windows."""

from __future__ import annotations

import os
import string
import warnings
from pathlib import Path

import tomllib

from mercury.core.paths import LOCAL_CONFIG
from mercury.core.platform import PlatformInfo, detect_platform

ENV_USB_MOUNT = "MERCURY_USB_MOUNT"
ENV_LEGACY_MOUNT = "MERCURY_LEGACY_MOUNT"
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
    Resolve the active transitional / legacy operator storage root.

    Until cutover this is the USB write root. Prefer role-specific env vars and
    ``[storage]`` active-write root so observe and write stacks stay aligned.

    Precedence (highest first):
    - MERCURY_LEGACY_MOUNT
    - MERCURY_USB_MOUNT (deprecated; transitional USB only)
    - load_storage_config().active_write_root while role is legacy
    - [mercury].usb_mount in config/local.toml
    - Windows auto-detect (drive letter with mercury_backups + mercury_logs)
    - DEFAULT_USB_MOUNT (/mnt/MERCURY_DATA_USB on Linux)
    """
    legacy_env = os.environ.get(ENV_LEGACY_MOUNT)
    if legacy_env and str(legacy_env).strip():
        return Path(str(legacy_env).strip()).expanduser().resolve()

    env_value = os.environ.get(ENV_USB_MOUNT)
    if env_value and str(env_value).strip():
        if os.environ.get("PYTEST_CURRENT_TEST") is None:
            warnings.warn(
                f"{ENV_USB_MOUNT} is deprecated; use {ENV_LEGACY_MOUNT} for the "
                "transitional USB role and MERCURY_PRIMARY_MOUNT for the canonical primary. "
                f"{ENV_USB_MOUNT} never selects primary storage after cutover.",
                DeprecationWarning,
                stacklevel=2,
            )
        return Path(str(env_value).strip()).expanduser().resolve()

    config_path = local_config or LOCAL_CONFIG
    try:
        from mercury.core.storage_roots import load_storage_config
        from mercury.core.storage_roles import StorageWriteRole

        storage = load_storage_config(local_config=config_path, warn_deprecated=False)
        if storage.active_write_role == StorageWriteRole.LEGACY:
            return storage.legacy.mount_path.expanduser().resolve()
        # After cutover, USB-named resolvers still point at the legacy archive root
        # so historical tooling does not silently retarget primary.
        return storage.legacy.mount_path.expanduser().resolve()
    except Exception:
        pass

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


def resolve_operator_mount(*, local_config: Path | None = None) -> Path:
    """Resolve the currently configured writer mount (legacy before cutover, primary after)."""
    config_path = local_config or LOCAL_CONFIG
    try:
        from mercury.core.storage_roots import load_storage_config
        from mercury.core.storage_roles import StorageWriteRole

        storage = load_storage_config(local_config=config_path, warn_deprecated=False)
        if storage.active_write_role == StorageWriteRole.LEGACY:
            # Preserve legacy environment overrides and compatibility hooks until
            # the operator actually selects the primary writer role.
            return resolve_usb_mount(local_config=config_path)
        return storage.active_write_root.mount_path.expanduser().resolve()
    except Exception:
        return resolve_usb_mount(local_config=config_path)


def storage_mount_is_active(mount_path: Path, *, platform_info: PlatformInfo | None = None) -> bool:
    """True when an operator-storage mount appears mounted and usable for Mercury."""
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


def inactive_operator_mount_blocker(path: Path, *, local_config: Path | None = None) -> str | None:
    """
    If ``path`` is under the configured operator mount and that mount is not active,
    return a refusal detail. Prevents shadow writes onto the NVMe under an empty
    mountpoint (e.g. /mnt/MERCURY_DATA_V2 when the HDD is unplugged).
    """
    try:
        mount = resolve_operator_mount(local_config=local_config).resolve()
        resolved = path.expanduser().resolve()
        resolved.relative_to(mount)
    except Exception:
        return None
    if storage_mount_is_active(mount):
        return None
    return f"operator mount not active: {mount}"


def usb_mount_is_active(mount_path: Path, *, platform_info: PlatformInfo | None = None) -> bool:
    """Legacy compatibility wrapper; prefer :func:`storage_mount_is_active`."""
    return storage_mount_is_active(mount_path, platform_info=platform_info)


def unmounted_storage_path_blocker(mount_path: Path) -> str | None:
    """
    Refuse writes into a directory that looks like a mount point but is not mounted.

    Does not delete or relocate stale entries.
    """
    path = Path(mount_path)
    if not path.exists():
        return None
    if usb_mount_is_active(path):
        return None
    try:
        from mercury.core.storage_validate import list_stale_mountpoint_entries

        stale = list_stale_mountpoint_entries(path)
    except Exception:
        stale = ()
    if stale:
        listing = ", ".join(stale[:6])
        return (
            f"Refusing backup execution because {path} is not an active mount and contains "
            f"unexpected entries ({listing}). Mount the configured volume or clear the "
            "stale mount-point directory manually — Mercury will not remove them."
        )
    return (
        f"Refusing backup execution because the required storage mount is not active: {path}"
    )


def storage_mount_label(mount_path: Path) -> str:
    """Display an operator-storage mount without assuming a USB device."""
    return str(mount_path)


def usb_mount_label(mount_path: Path) -> str:
    """Legacy compatibility wrapper; prefer :func:`storage_mount_label`."""
    return storage_mount_label(mount_path)


def default_usb_path_replacements(mount_path: Path) -> dict[str, str]:
    """Legacy compatibility wrapper; prefer operator-storage path replacements."""
    return default_operator_path_replacements(mount_path)


def default_operator_path_replacements(mount_path: Path) -> dict[str, str]:
    """Standard Mercury subpaths under an active operator-storage mount."""
    root = mount_path.resolve()
    return {
        "backup_root": str(root / "mercury_backups"),
        "log_dir": str(root / "mercury_logs"),
        "repo_backup_root": str(root / "mercury_repo_backups"),
        "manifest_dir": str(root / "mercury_manifests"),
        "runbook_dir": str(root / "mercury_runbooks"),
    }


def assert_operator_usb_path(path: Path, *, usb_mount: Path | None = None) -> None:
    """Refuse writes outside the resolved operator storage mount when it is not active."""
    mount = (usb_mount or resolve_usb_mount()).resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(mount)
    except ValueError as exc:
        raise ValueError(f"path is not under {mount}: {resolved}") from exc
    if not usb_mount_is_active(mount):
        raise ValueError(f"required operator storage mount is not active: {mount}")


def assert_operator_storage_path(path: Path, *, operator_mount: Path | None = None) -> None:
    """Require a write path under the configured active operator-storage role."""
    mount = (operator_mount or resolve_operator_mount()).resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(mount)
    except ValueError as exc:
        raise ValueError(f"path is not under active operator storage {mount}: {resolved}") from exc
    if not usb_mount_is_active(mount):
        raise ValueError(f"required operator storage mount is not active: {mount}")
