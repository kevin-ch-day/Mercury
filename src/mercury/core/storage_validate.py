"""Mount and identity validation for Mercury storage roots."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from mercury.core.storage_roles import MountValidationCode
from mercury.core.storage_space import SpaceAssessment, SpacePolicy, assess_space


@dataclass(frozen=True)
class MountIdentity:
    """Observed identity of a path that may or may not be a mount."""

    mount_path: Path
    path_exists: bool
    is_mount: bool
    mounted_uuid: str | None = None
    mounted_fstype: str | None = None
    mount_options: str | None = None
    writable: bool | None = None
    capacity_bytes: int | None = None
    available_bytes: int | None = None
    stale_mountpoint_entries: tuple[str, ...] = ()


@dataclass(frozen=True)
class MountValidationResult:
    """Result of validating a configured storage root before use."""

    code: MountValidationCode
    mount_path: Path
    expected_uuid: str | None
    expected_fstype: str | None
    identity: MountIdentity
    space: SpaceAssessment | None = None
    messages: tuple[str, ...] = ()
    blocker: str | None = None

    @property
    def ok(self) -> bool:
        return self.code == MountValidationCode.OK and self.blocker is None


def _read_proc_mounts() -> list[tuple[str, str, str, str]]:
    """Return (device, target, fstype, options) rows from /proc/mounts."""
    rows: list[tuple[str, str, str, str]] = []
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


def _unescape_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def find_mount_row(mount_path: Path) -> tuple[str, str, str, str] | None:
    """Locate the /proc/mounts row whose target matches mount_path."""
    try:
        resolved = mount_path.resolve()
    except OSError:
        resolved = mount_path
    for device, target, fstype, options in _read_proc_mounts():
        try:
            target_path = Path(_unescape_mount_field(target)).resolve()
        except OSError:
            target_path = Path(_unescape_mount_field(target))
        if target_path == resolved:
            return device, target, fstype, options
    return None


def resolve_uuid_for_device(device: str) -> str | None:
    """Map a /dev path to its filesystem UUID via /dev/disk/by-uuid."""
    by_uuid = Path("/dev/disk/by-uuid")
    if not by_uuid.is_dir():
        return None
    try:
        device_resolved = Path(device).resolve()
    except OSError:
        device_resolved = Path(device)
    try:
        for entry in by_uuid.iterdir():
            try:
                if entry.resolve() == device_resolved:
                    return entry.name
            except OSError:
                continue
    except OSError:
        return None
    return None


def list_stale_mountpoint_entries(mount_path: Path) -> tuple[str, ...]:
    """
    List unexpected entries beneath a directory that is not an active mount.

    Does not delete or relocate anything.
    """
    if not mount_path.exists() or not mount_path.is_dir():
        return ()
    if find_mount_row(mount_path) is not None:
        return ()
    try:
        is_mount = mount_path.is_mount()
    except OSError:
        is_mount = False
    if is_mount:
        return ()
    names: list[str] = []
    try:
        for child in sorted(mount_path.iterdir(), key=lambda p: p.name):
            names.append(child.name)
    except OSError:
        return ()
    return tuple(names)


def path_is_writable(path: Path) -> bool:
    """True when the process can create a file under path."""
    if not path.exists() or not path.is_dir():
        return False
    try:
        return os.access(path, os.W_OK | os.X_OK)
    except OSError:
        return False


def probe_mount_identity(mount_path: Path) -> MountIdentity:
    """Observe mount/path identity without applying policy."""
    path = Path(mount_path)
    exists = path.exists()
    row = find_mount_row(path) if exists else None
    is_mount = row is not None
    if not is_mount and exists:
        try:
            is_mount = path.is_mount()
        except OSError:
            is_mount = False

    mounted_uuid: str | None = None
    mounted_fstype: str | None = None
    mount_options: str | None = None
    if row is not None:
        device, _target, mounted_fstype, mount_options = row
        mounted_uuid = resolve_uuid_for_device(device)

    writable: bool | None = None
    capacity: int | None = None
    available: int | None = None
    if exists and path.is_dir():
        writable = path_is_writable(path)
        try:
            usage = shutil.disk_usage(path)
            capacity = int(usage.total)
            available = int(usage.free)
        except OSError:
            pass

    stale = list_stale_mountpoint_entries(path) if exists and not is_mount else ()

    return MountIdentity(
        mount_path=path,
        path_exists=exists,
        is_mount=is_mount,
        mounted_uuid=mounted_uuid,
        mounted_fstype=mounted_fstype,
        mount_options=mount_options,
        writable=writable,
        capacity_bytes=capacity,
        available_bytes=available,
        stale_mountpoint_entries=stale,
    )


def _options_indicate_readonly(options: str | None) -> bool:
    if not options:
        return False
    parts = {part.strip() for part in options.split(",")}
    return "ro" in parts and "rw" not in parts


def validate_storage_mount(
    *,
    mount_path: Path,
    expected_uuid: str | None,
    expected_fstype: str | None = "ext4",
    require_writable: bool = True,
    space_policy: SpacePolicy | None = None,
    estimated_operation_bytes: int = 0,
    identity: MountIdentity | None = None,
) -> MountValidationResult:
    """
    Validate that mount_path is the expected filesystem and usable for writes.

    Directory existence alone is never sufficient.
    """
    observed = identity or probe_mount_identity(mount_path)
    messages: list[str] = []
    policy = space_policy or SpacePolicy()

    if not observed.path_exists:
        return MountValidationResult(
            code=MountValidationCode.MOUNT_PATH_MISSING,
            mount_path=Path(mount_path),
            expected_uuid=expected_uuid,
            expected_fstype=expected_fstype,
            identity=observed,
            messages=("Mount path does not exist.",),
            blocker=f"mount path missing: {mount_path}",
        )

    if observed.stale_mountpoint_entries and not observed.is_mount:
        listing = ", ".join(observed.stale_mountpoint_entries[:8])
        messages.append(
            f"Unmounted mount point contains unexpected entries: {listing}"
            + ("…" if len(observed.stale_mountpoint_entries) > 8 else "")
        )

    if not observed.is_mount:
        blocker = f"directory present but not mounted: {mount_path}"
        code = (
            MountValidationCode.STALE_MOUNTPOINT_CONTENT
            if observed.stale_mountpoint_entries
            else MountValidationCode.NOT_A_MOUNT
        )
        return MountValidationResult(
            code=code,
            mount_path=Path(mount_path),
            expected_uuid=expected_uuid,
            expected_fstype=expected_fstype,
            identity=observed,
            messages=tuple(messages) or ("Path exists but is not an active mount.",),
            blocker=blocker,
        )

    if expected_uuid and observed.mounted_uuid and observed.mounted_uuid != expected_uuid:
        return MountValidationResult(
            code=MountValidationCode.WRONG_UUID,
            mount_path=Path(mount_path),
            expected_uuid=expected_uuid,
            expected_fstype=expected_fstype,
            identity=observed,
            messages=(
                f"Mounted UUID {observed.mounted_uuid} does not match expected {expected_uuid}.",
            ),
            blocker=(
                f"wrong filesystem mounted at {mount_path}: "
                f"got {observed.mounted_uuid}, expected {expected_uuid}"
            ),
        )

    if expected_uuid and not observed.mounted_uuid:
        return MountValidationResult(
            code=MountValidationCode.WRONG_UUID,
            mount_path=Path(mount_path),
            expected_uuid=expected_uuid,
            expected_fstype=expected_fstype,
            identity=observed,
            messages=("Unable to resolve mounted filesystem UUID.",),
            blocker=f"unable to resolve UUID for mount at {mount_path}",
        )

    if expected_fstype and observed.mounted_fstype and observed.mounted_fstype != expected_fstype:
        return MountValidationResult(
            code=MountValidationCode.WRONG_FSTYPE,
            mount_path=Path(mount_path),
            expected_uuid=expected_uuid,
            expected_fstype=expected_fstype,
            identity=observed,
            messages=(
                f"Mounted filesystem type {observed.mounted_fstype} "
                f"does not match expected {expected_fstype}.",
            ),
            blocker=(
                f"wrong filesystem type at {mount_path}: "
                f"got {observed.mounted_fstype}, expected {expected_fstype}"
            ),
        )

    readonly = _options_indicate_readonly(observed.mount_options) or observed.writable is False
    if require_writable and readonly:
        return MountValidationResult(
            code=MountValidationCode.READ_ONLY,
            mount_path=Path(mount_path),
            expected_uuid=expected_uuid,
            expected_fstype=expected_fstype,
            identity=observed,
            messages=("Filesystem is mounted read-only or not writable.",),
            blocker=f"filesystem mounted read-only: {mount_path}",
        )

    space: SpaceAssessment | None = None
    if (
        observed.capacity_bytes is not None
        and observed.available_bytes is not None
        and require_writable
    ):
        space = assess_space(
            policy,
            capacity_bytes=observed.capacity_bytes,
            available_bytes=observed.available_bytes,
            estimated_operation_bytes=estimated_operation_bytes,
        )
        messages.append(space.summary())
        if not space.passes:
            return MountValidationResult(
                code=MountValidationCode.INSUFFICIENT_SPACE,
                mount_path=Path(mount_path),
                expected_uuid=expected_uuid,
                expected_fstype=expected_fstype,
                identity=observed,
                space=space,
                messages=tuple(messages),
                blocker=(
                    f"insufficient free space at {mount_path}: {space.summary()}"
                ),
            )

    return MountValidationResult(
        code=MountValidationCode.OK,
        mount_path=Path(mount_path),
        expected_uuid=expected_uuid,
        expected_fstype=expected_fstype,
        identity=observed,
        space=space,
        messages=tuple(messages),
        blocker=None,
    )
