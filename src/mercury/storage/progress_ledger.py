"""Append-only migration progress ledger under primary .mercury_control."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.storage_roles import CONTROL_DIRNAME

LEDGER_NAME = "migration_progress.jsonl"


def ledger_path(primary_mount: Path) -> Path:
    return primary_mount / CONTROL_DIRNAME / LEDGER_NAME


def ensure_ledger(primary_mount: Path) -> Path:
    from mercury.core.usb_mount import inactive_operator_mount_blocker

    path = ledger_path(primary_mount)
    # Refuse shadow writes onto an empty configured mountpoint; allow explicit
    # temp/test primary paths that are not under the live operator mount.
    inactive = inactive_operator_mount_blocker(path)
    if inactive:
        raise OSError(f"Refusing migration ledger write ({inactive})")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def append_progress(
    primary_mount: Path,
    *,
    relative_path: str,
    action: str,
    status: str,
    bytes_copied: int = 0,
    detail: str | None = None,
) -> None:
    path = ensure_ledger(primary_mount)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "relative_path": relative_path,
        "action": action,
        "status": status,
        "bytes_copied": bytes_copied,
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def completed_paths(primary_mount: Path) -> set[str]:
    """Return relative paths with a successful status in the ledger."""
    path = ledger_path(primary_mount)
    if not path.exists():
        return set()
    done: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") == "ok" and row.get("relative_path"):
                done.add(str(row["relative_path"]))
    except OSError:
        return set()
    return done


def clear_ledger(primary_mount: Path) -> None:
    path = ledger_path(primary_mount)
    if path.exists():
        path.write_text("", encoding="utf-8")
