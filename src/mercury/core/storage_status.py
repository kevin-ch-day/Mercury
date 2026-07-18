"""Shared storage-status helpers for operator screens."""

from __future__ import annotations

from pathlib import Path

from mercury.terminal.format import format_bytes


def backup_root_mount_label(policy, *, styled: bool = False) -> str:
    state = policy.backup_root_state()
    if state == "usb-mounted":
        return "[ok] mounted" if styled else "mounted"
    if state == "repo-local fallback":
        return "[!!] repo-local fallback" if styled else "repo-local fallback"
    if state == "usb not mounted":
        return "[!!] not mounted" if styled else "not mounted"
    if state == "unsafe path":
        return "[!!] unsafe path" if styled else "unsafe path"
    if state == "missing path":
        return "[!!] missing path" if styled else "missing path"
    if state == "low free space":
        return "[--] mounted" if styled else "mounted"
    return "[--] unknown" if styled else "unknown"


def backup_root_filesystem(path: Path) -> str | None:
    mount_info = _mount_info(path)
    if mount_info is None:
        return None
    return mount_info[1]


def backup_root_free_space_label(policy) -> str | None:
    free_bytes = policy.backup_root_free_bytes()
    if free_bytes is None:
        return None
    return format_bytes(free_bytes)


def backup_root_storage_status_label(policy, *, styled: bool = False) -> str:
    state = policy.backup_root_state()
    if state == "usb-mounted":
        return "[ok] ready" if styled else "ready"
    if state == "low free space":
        return "[--] warning" if styled else "warning"
    return "[!!] unsafe" if styled else "unsafe"


def backup_root_summary_label(policy) -> str:
    state = policy.backup_root_state()
    target = str(policy.backup_root.resolve())
    if state == "usb-mounted":
        return "[ok] storage ready"
    if state == "low free space":
        return "[--] storage warning"
    if state == "repo-local fallback":
        return f"[!!] repo-local fallback — {target}"
    if state == "usb not mounted":
        return "[!!] storage not mounted"
    if state == "unsafe path":
        return f"[!!] unsafe path — {target}"
    if state == "missing path":
        return f"[!!] missing path — {target}"
    return "[--] unknown"


def _mount_info(path: Path) -> tuple[Path, str] | None:
    try:
        with Path("/proc/mounts").open("r", encoding="utf-8") as handle:
            mounts = []
            for line in handle:
                parts = line.split()
                if len(parts) >= 3:
                    mounts.append((Path(parts[1]), parts[2]))
    except OSError:
        return None

    resolved = path.resolve()
    matches = [(mount_path, fs_type) for mount_path, fs_type in mounts if str(resolved).startswith(str(mount_path))]
    if not matches:
        return None
    return max(matches, key=lambda item: len(str(item[0])))
