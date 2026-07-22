"""Path ownership and writability checks for operator setup."""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathPermissionCheck:
    path: Path
    label: str
    exists: bool
    writable: bool
    owner: str | None
    owner_mismatch: bool
    detail: str

    @property
    def needs_repair(self) -> bool:
        if not self.exists:
            return not self.writable
        return not self.writable or self.owner_mismatch


def _owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def can_append(path: Path) -> bool:
    """Return whether the current user can append to an existing path."""
    try:
        with path.open("a", encoding="utf-8"):
            pass
        return True
    except (PermissionError, OSError):
        return False


def _blocking_file_in_directory(path: Path) -> str | None:
    """Return detail when an existing file blocks append despite a writable-looking directory."""
    if not path.is_dir():
        return None
    probe_names = ("operations.jsonl", "error.log", "database.log", "backup.log")
    for name in probe_names:
        candidate = path / name
        if candidate.exists() and not can_append(candidate):
            try:
                owner = _owner_name(candidate.stat().st_uid)
            except OSError:
                owner = "unknown"
            return f"existing file not writable: {name} (owner: {owner})"
    for candidate in sorted(path.glob("mercury-*.log")):
        if not can_append(candidate):
            try:
                owner = _owner_name(candidate.stat().st_uid)
            except OSError:
                owner = "unknown"
            return f"existing file not writable: {candidate.name} (owner: {owner})"
    return None


def _probe_directory_writable(path: Path) -> tuple[bool, str]:
    blocked = _blocking_file_in_directory(path)
    if blocked:
        return False, blocked
    probe = path / ".mercury_write_probe"
    try:
        with probe.open("a", encoding="utf-8") as handle:
            handle.write("")
        probe.unlink(missing_ok=True)
        return True, "ok"
    except (PermissionError, OSError):
        try:
            owner = _owner_name(path.stat().st_uid)
        except OSError:
            owner = "unknown"
        if path.stat().st_uid != os.geteuid():
            return False, f"not writable (owner: {owner})"
        return False, "not writable"


def check_path_permission(path: Path, *, label: str) -> PathPermissionCheck:
    """Assess whether the current user can use a Mercury directory."""
    resolved = path.expanduser()
    exists = resolved.exists()
    owner: str | None = None
    owner_mismatch = False
    writable = False
    detail = "ok"

    from mercury.core.usb_mount import inactive_operator_mount_blocker

    inactive = inactive_operator_mount_blocker(resolved)
    if inactive:
        return PathPermissionCheck(
            path=resolved,
            label=label,
            exists=exists,
            writable=False,
            owner=None,
            owner_mismatch=False,
            detail=inactive,
        )

    if exists:
        if resolved.is_dir():
            writable, detail = _probe_directory_writable(resolved)
        else:
            writable = can_append(resolved)
        try:
            owner = _owner_name(resolved.stat().st_uid)
            owner_mismatch = resolved.stat().st_uid != os.geteuid() and not writable
        except OSError as exc:
            detail = str(exc)
            writable = False
        if not writable and detail == "ok":
            if owner_mismatch:
                detail = f"not writable (owner: {owner})"
            else:
                detail = "not writable"
    else:
        parent = resolved.parent
        if parent.exists():
            writable, detail = _probe_directory_writable(parent) if parent.is_dir() else (can_append(parent), "ok")
            if not writable and detail == "ok":
                detail = "missing and parent not writable"
            elif writable:
                detail = "missing but parent writable"
        else:
            detail = f"missing (parent {parent} not found)"
            writable = False

    return PathPermissionCheck(
        path=resolved,
        label=label,
        exists=exists,
        writable=writable,
        owner=owner,
        owner_mismatch=owner_mismatch,
        detail=detail,
    )


def safe_ensure_directory(path: Path) -> tuple[bool, str]:
    """Create a missing directory when the parent is writable (non-privileged self-heal)."""
    resolved = path.expanduser()
    if resolved.exists():
        return True, "already exists"
    from mercury.core.usb_mount import inactive_operator_mount_blocker

    inactive = inactive_operator_mount_blocker(resolved)
    if inactive:
        return False, inactive
    parent = resolved.parent
    if not parent.exists():
        return False, f"parent missing: {parent}"
    parent_writable, detail = _probe_directory_writable(parent) if parent.is_dir() else (can_append(parent), "")
    if not parent_writable:
        return False, detail or f"parent not writable: {parent}"
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        return True, "created"
    except OSError as exc:
        return False, str(exc)


def chown_repair_command(path: Path) -> str:
    user = os.environ.get("USER") or pwd.getpwuid(os.geteuid()).pw_name
    return f'sudo chown -R "{user}:{user}" {path}'
