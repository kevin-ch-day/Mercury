"""Redirect Mercury logging off the HDD before safe disconnect."""

from __future__ import annotations

import os
from pathlib import Path

from mercury.core.storage_roles import DEFAULT_PRIMARY_MOUNT

ENV_DETACH_LOG_DIR = "MERCURY_DETACH_LOG_DIR"


def default_detach_log_dir() -> Path:
    override = os.environ.get(ENV_DETACH_LOG_DIR)
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "mercury" / "detach_logs"


def list_self_fds_under_mount(mount: str | Path) -> list[str]:
    """Return /proc/self/fd targets that resolve beneath the Mercury mount."""
    mount_s = str(Path(mount).resolve()) if Path(mount).exists() else str(mount)
    found: list[str] = []
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        return found
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        # Skip anonymized fds
        if target.startswith(("pipe:", "socket:", "anon_inode:")):
            continue
        # Match mount prefix (even if mount is currently unmounted, path strings may linger)
        if target == mount_s or target.startswith(mount_s + "/"):
            found.append(target)
        # Also catch unresolved mount path strings
        elif target.startswith(str(mount)) or target.startswith(
            DEFAULT_PRIMARY_MOUNT
        ):
            if str(mount) in target or DEFAULT_PRIMARY_MOUNT in target:
                if target.startswith(str(mount)) or target.startswith(
                    DEFAULT_PRIMARY_MOUNT
                ):
                    found.append(target)
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def redirect_logging_off_hdd(
    *,
    mount: str | Path = DEFAULT_PRIMARY_MOUNT,
    log_dir: Path | None = None,
) -> tuple[Path, list[str]]:
    """Close HDD log handlers and reopen under a host-local detach log directory.

    Returns (new_log_dir, remaining_self_fds_under_mount).
    """
    from mercury.logging.engine import configure_logging, reset_logging

    dest = log_dir or default_detach_log_dir()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
    except OSError:
        pass

    reset_logging()
    configure_logging(log_dir=dest)

    remaining = list_self_fds_under_mount(mount)
    return dest, remaining
