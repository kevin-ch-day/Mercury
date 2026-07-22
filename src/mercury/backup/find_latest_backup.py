"""Locate on-disk backup artifact directories with explicit selection semantics."""

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


def _manifest_payload(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


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
        if not db_dir.is_dir():
            continue
        # Read legacy date/database manifests and the immutable
        # date/database/timestamp run directories used by new backups.
        if (db_dir / MANIFEST_FILENAME).is_file():
            found.append(db_dir)
        found.extend(
            child
            for child in sorted(db_dir.iterdir())
            if child.is_dir() and (child / MANIFEST_FILENAME).is_file()
        )
    return found


def _sort_key(path: Path) -> tuple[float, float]:
    created = _manifest_created_at(path / MANIFEST_FILENAME)
    created_ts = created.timestamp() if created is not None else 0.0
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (created_ts, mtime)


def find_latest_backup_directory(backup_root: Path, database: str) -> Path | None:
    """Return the newest written backup directory (manifest created_at, then mtime).

    This is ``latest_written`` only. It does **not** filter by artifact
    verification, restore-check, package membership, or handoff eligibility.
    Prefer ``find_backup_by_id``, ``find_latest_artifact_verified_backup``, or
    ``find_latest_restore_checked_backup`` for migration and destination work.
    """
    candidates = find_backup_directories(backup_root, database)
    if not candidates:
        return None
    return max(candidates, key=_sort_key)


def find_backup_by_id(backup_root: Path, backup_id: str, *, database: str | None = None) -> Path | None:
    """Return the backup directory whose manifest backup_id matches exactly."""
    wanted = (backup_id or "").strip()
    if not wanted or not backup_root.is_dir():
        return None
    databases: list[str]
    if database:
        databases = [database]
    else:
        databases = []
        try:
            for day_dir in backup_root.iterdir():
                if not day_dir.is_dir():
                    continue
                for db_dir in day_dir.iterdir():
                    if db_dir.is_dir():
                        databases.append(db_dir.name)
        except OSError:
            return None
        databases = sorted(set(databases))

    matches: list[Path] = []
    for name in databases:
        for candidate in find_backup_directories(backup_root, name):
            payload = _manifest_payload(candidate / MANIFEST_FILENAME)
            if str(payload.get("backup_id") or "").strip() == wanted:
                matches.append(candidate)
    if not matches:
        return None
    return max(matches, key=_sort_key)


def find_latest_artifact_verified_backup(
    backup_root: Path,
    database: str,
    *,
    allow_development_backup: bool = False,
) -> Path | None:
    """Newest backup directory that currently passes on-disk artifact integrity checks.

    Candidates are checked newest-first and return on the first integrity pass so
    doctor/status paths do not checksum every historical dump.
    """
    from mercury.backup.verification import verify_backup_artifacts

    candidates = sorted(find_backup_directories(backup_root, database), key=_sort_key, reverse=True)
    for path in candidates:
        if verify_backup_artifacts(
            path,
            database=database,
            allow_development_backup=allow_development_backup,
        ).verified:
            return path
    return None


def find_latest_restore_checked_backup(
    backup_root: Path,
    database: str,
    *,
    require_passed: bool = True,
) -> Path | None:
    """Newest on-disk backup whose exact backup_id has a ledger restore-check result."""
    from mercury.backup.status import latest_restore_check_by_backup_id

    records = latest_restore_check_by_backup_id()
    matches: list[Path] = []
    for path in find_backup_directories(backup_root, database):
        payload = _manifest_payload(path / MANIFEST_FILENAME)
        backup_id = str(payload.get("backup_id") or "").strip()
        record = records.get(backup_id)
        if record is None:
            continue
        if require_passed and record.status != "passed":
            continue
        matches.append(path)
    if not matches:
        return None
    return max(matches, key=_sort_key)


class BackupSelectionError(ValueError):
    """Requested backup identity cannot be resolved safely."""


def resolve_backup_directory(
    backup_root: Path,
    database: str,
    *,
    backup_id: str | None = None,
    prefer: str = "artifact_verified",
    allow_unverified: bool = False,
) -> Path:
    """Resolve a backup directory by exact ID or a documented preference.

    ``prefer`` values:
      - ``written``: newest on disk (inventory only)
      - ``artifact_verified``: newest that passes integrity checks
      - ``restore_checked``: newest with ledger restore-check passed
    """
    if backup_id:
        path = find_backup_by_id(backup_root, backup_id, database=database)
        if path is None:
            raise BackupSelectionError(
                f"No backup directory found for database '{database}' with backup_id '{backup_id}'."
            )
        return path

    if prefer == "restore_checked":
        path = find_latest_restore_checked_backup(backup_root, database)
        if path is not None:
            return path
        raise BackupSelectionError(
            f"No restore-checked backup found for '{database}'. Pass --backup-id explicitly."
        )
    if prefer == "written":
        path = find_latest_backup_directory(backup_root, database)
        if path is None:
            raise BackupSelectionError(f"No on-disk backup found for '{database}'.")
        return path

    # Default: artifact_verified
    path = find_latest_artifact_verified_backup(backup_root, database)
    if path is not None:
        return path
    if allow_unverified:
        path = find_latest_backup_directory(backup_root, database)
        if path is not None:
            return path
    raise BackupSelectionError(
        f"No artifact-verified backup found for '{database}'. "
        "Pass --backup-id for an explicit selection, or create/verify a full backup first."
    )


def list_backup_candidates(backup_root: Path, database: str) -> list[dict[str, str | bool | None]]:
    """Return candidate backups for interactive selection (newest first).

    Uses manifest stamps and ledger status for listing; does not checksum dumps.
    """
    from mercury.backup.status import latest_restore_check_by_backup_id
    from mercury.backup.verification import manifest_verified_stamp

    records = latest_restore_check_by_backup_id()
    rows: list[dict[str, str | bool | None]] = []
    for path in sorted(find_backup_directories(backup_root, database), key=_sort_key, reverse=True):
        payload = _manifest_payload(path / MANIFEST_FILENAME)
        backup_id = str(payload.get("backup_id") or "").strip() or None
        stamp = manifest_verified_stamp(path / MANIFEST_FILENAME)
        # Listing hint only — operators must still run verify/restore-check by ID.
        artifact_ok = bool(payload.get("sha256")) and bool(payload.get("dump_file"))
        rc = records.get(backup_id) if backup_id else None
        rows.append(
            {
                "backup_id": backup_id,
                "directory": str(path),
                "created_at": str(payload.get("created_at") or "") or None,
                "artifact_integrity_verified": artifact_ok,
                "manifest_verification_stamp": stamp,
                "restore_check_status": rc.status if rc else None,
            }
        )
    return rows
