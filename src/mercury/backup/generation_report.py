"""Backup generation identity reporting without re-hashing dump contents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mercury.backup.find_latest_backup import (
    find_backup_directories,
    find_latest_backup_directory,
    find_latest_restore_checked_backup,
)
from mercury.backup.layout import MANIFEST_FILENAME

PRODUCTION_REPORT_DATABASES: tuple[str, ...] = (
    "android_permission_intel",
    "erebus_threat_intel_prod",
    "obsidiandroid_core_prod",
    "scytaledroid_core_prod",
)


@dataclass(frozen=True)
class BackupGenerationIdentities:
    database: str
    latest_written_backup_id: str | None
    latest_artifact_verified_id: str | None
    latest_manifest_stamped_id: str | None
    latest_restore_checked_id: str | None
    latest_written_path: str | None = None
    notes: tuple[str, ...] = ()


def _manifest_payload(path: Path) -> dict:
    manifest = path / MANIFEST_FILENAME
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _backup_id(path: Path) -> str | None:
    value = str(_manifest_payload(path).get("backup_id") or "").strip()
    return value or None


def _created_ts(path: Path) -> float:
    raw = _manifest_payload(path).get("created_at")
    if not raw:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _manifest_stamped(path: Path) -> bool:
    return _manifest_payload(path).get("verified") is True


def _artifact_presence_ok(path: Path) -> bool:
    """Lightweight integrity contract: checksum file + dump size match (no re-hash)."""
    payload = _manifest_payload(path)
    dump_name = payload.get("dump_file")
    size_bytes = payload.get("size_bytes")
    if not dump_name:
        return False
    dump = path / str(dump_name)
    try:
        if not dump.is_file():
            return False
        if size_bytes is not None and dump.stat().st_size != int(size_bytes):
            return False
    except (OSError, TypeError, ValueError):
        return False
    checksum = path / "checksum.sha256"
    return checksum.is_file()


def report_backup_generation_identities(
    backup_root: Path,
    *,
    databases: tuple[str, ...] | None = None,
) -> list[BackupGenerationIdentities]:
    """Report latest written / stamped / presence-verified / restore-checked IDs.

    Does not modify manifests and does not recompute dump SHA-256 digests.
    """
    names = databases or PRODUCTION_REPORT_DATABASES
    rows: list[BackupGenerationIdentities] = []
    for database in names:
        written = find_latest_backup_directory(backup_root, database)
        candidates = find_backup_directories(backup_root, database)
        stamped = [path for path in candidates if _manifest_stamped(path)]
        artifact = [path for path in candidates if _artifact_presence_ok(path)]
        restore = find_latest_restore_checked_backup(backup_root, database)
        latest_stamped = max(stamped, key=_created_ts) if stamped else None
        latest_artifact = max(artifact, key=_created_ts) if artifact else None
        notes: list[str] = []
        written_id = _backup_id(written) if written else None
        restore_id = _backup_id(restore) if restore else None
        if written_id and restore_id and written_id != restore_id:
            notes.append(
                "Latest written generation differs from latest restore-checked "
                "(Phase 3B pin may be older than routine 16:40 UTC runs)."
            )
        rows.append(
            BackupGenerationIdentities(
                database=database,
                latest_written_backup_id=written_id,
                latest_artifact_verified_id=(
                    _backup_id(latest_artifact) if latest_artifact else None
                ),
                latest_manifest_stamped_id=(
                    _backup_id(latest_stamped) if latest_stamped else None
                ),
                latest_restore_checked_id=restore_id,
                latest_written_path=str(written) if written else None,
                notes=tuple(notes),
            )
        )
    return rows
