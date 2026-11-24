"""Strict Linux volume admission and bounded activation of an existing automount."""

from __future__ import annotations

import fcntl
import os
import shutil
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path


def mount_records(
    mount: str, text: str | None = None
) -> list[tuple[str, str, str, set[str]]]:
    text = Path("/proc/self/mountinfo").read_text() if text is None else text
    records = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if sep and len(fields) >= 6 and len(tail) >= 3:
            target = fields[4].replace("\\040", " ").replace("\\134", "\\")
            if target == os.path.normpath(mount):
                records.append(
                    (
                        fields[2],
                        tail[0],
                        tail[1],
                        set(fields[5].split(",")) | set(tail[2].split(",")),
                    )
                )
    return records


def require_disk(
    mount: str, uuid: str, *, mountinfo: str | None = None, device_stat=None
) -> None:
    records = [
        r
        for r in mount_records(mount, mountinfo)
        if r[1] != "autofs" and r[0].split(":")[0] != "0"
    ]
    if len(records) != 1:
        raise ValueError(
            "Mercury repository sync refused: real backup disk is not mounted (autofs alone is insufficient). No backup writes attempted."
        )
    dev, fs, source, options = records[0]
    if fs != "ext4" or "ro" in options or "rw" not in options:
        raise ValueError("Mercury destination requires a writable ext4 filesystem")
    if not source.startswith("/dev/"):
        raise ValueError("Mercury mount source is not a block device")
    info = (device_stat or os.stat)("/dev/disk/by-uuid/" + uuid)
    if (
        not stat.S_ISBLK(info.st_mode)
        or dev != f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
    ):
        raise ValueError(
            "Mercury repository sync refused: mounted disk UUID does not match. No backup writes attempted."
        )
    source_info = (device_stat or os.stat)(source)
    if not stat.S_ISBLK(source_info.st_mode) or source_info.st_rdev != info.st_rdev:
        raise ValueError("Mercury mount source differs from the expected UUID device")


def activate_disk(mount: str, uuid: str) -> None:
    """Read-trigger only the exact ext4 automount already approved in fstab.

    User services cannot depend on system-manager mount units. Traversal asks
    the existing system automount to activate; no mount command or new options.
    """
    records = mount_records(mount)
    if any(r[1] != "autofs" for r in records):
        require_disk(mount, uuid)
        return
    if not any(r[1] == "autofs" for r in records):
        raise ValueError(
            "Expected Mercury automount is absent; no activation attempted"
        )
    approved = False
    for line in Path("/etc/fstab").read_text().splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#") or len(fields) < 4:
            continue
        if (
            fields[0] in {f"UUID={uuid}", f"/dev/disk/by-uuid/{uuid}"}
            and fields[1] == mount
            and fields[2] == "ext4"
            and "x-systemd.automount" in fields[3].split(",")
        ):
            approved = True
    if not approved:
        raise ValueError(
            "Mercury automount does not match approved fstab UUID/type/path"
        )
    try:
        subprocess.run(
            ["stat", "--", mount.rstrip("/") + "/."],
            check=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            "Mercury mount activation failed or timed out; no backup writes attempted"
        ) from exc
    require_disk(mount, uuid)


def require_destination(mount: str, uuid: str, destination: Path) -> None:
    require_disk(mount, uuid)
    root = Path(mount)
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("Destination must be an absolute path without traversal")
    relative = destination.relative_to(root)
    for p in [
        root,
        *(
            root.joinpath(*relative.parts[:i])
            for i in range(1, len(relative.parts) + 1)
        ),
    ]:
        if p.is_symlink():
            raise ValueError("Backup destination contains a symlink")
        if p.exists() and p.stat().st_dev != root.stat().st_dev:
            raise ValueError("Backup destination escapes the verified filesystem")


def require_headroom(mount: Path, additional_bytes: int) -> None:
    """Retain the configured Mercury reserve plus bounded job headroom."""
    from mercury.core.storage_roots import load_storage_config

    policy = load_storage_config().space_policy
    usage = shutil.disk_usage(mount)
    reserve = max(
        policy.minimum_free_bytes, int(usage.total * policy.minimum_free_percent / 100)
    )
    if additional_bytes < 0 or usage.free < reserve + additional_bytes:
        raise ValueError(
            "Mercury free space is below the existing reserve plus job headroom"
        )


@contextmanager
def exclusive_lock(path: Path):
    """Nonblocking lock with no symlink following; lock files are retained."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Backup lock is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another backup operation owns this lock") from exc
        yield
    finally:
        os.close(fd)
