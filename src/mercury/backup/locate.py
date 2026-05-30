"""Locate on-disk backup artifact directories."""

from __future__ import annotations

from pathlib import Path

from mercury.backup.layout import MANIFEST_FILENAME


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
    """Return the most recently modified backup directory for a database."""
    candidates = find_backup_directories(backup_root, database)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)
