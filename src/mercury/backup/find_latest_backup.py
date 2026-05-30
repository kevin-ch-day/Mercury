"""Locate on-disk backup artifact directories."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mercury.backup.layout import MANIFEST_FILENAME


def _manifest_created_at(path: Path) -> datetime | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("created_at")
        if not raw:
            return None
        if isinstance(raw, datetime):
            return raw
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def find_backup_directories(backup_root: Path, database: str) -> list[Path]:
    """Return backup dirs for a database that contain manifest.json (newest last)."""
    if not backup_root.is_dir():
        return []

    found: list[Path] = []
    try:
        day_dirs = sorted(backup_root.iterdir())
    except OSError:
        return []
    for day_dir in day_dirs:
        if not day_dir.is_dir():
            continue
        db_dir = day_dir / database
        if db_dir.is_dir() and (db_dir / MANIFEST_FILENAME).is_file():
            found.append(db_dir)
    return found


def find_latest_backup_directory(backup_root: Path, database: str) -> Path | None:
    """Return the newest backup directory for a database (manifest created_at, then mtime)."""
    candidates = find_backup_directories(backup_root, database)
    if not candidates:
        return None

    def sort_key(path: Path) -> tuple[float, float]:
        created = _manifest_created_at(path / MANIFEST_FILENAME)
        created_ts = created.timestamp() if created is not None else 0.0
        return (created_ts, path.stat().st_mtime)

    return max(candidates, key=sort_key)
