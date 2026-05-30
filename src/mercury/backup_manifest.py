"""Backup manifest model (planning; no writes in seed by default)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from mercury.backup_layout import (
    build_backup_layout,
    list_standard_filenames,
    planned_backup_directory,
    planned_dump_filename,
)
from mercury.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

BackupKind = Literal["full", "schema_only"]

# Re-export layout helpers for backward compatibility
planned_backup_dir = planned_backup_directory


def planned_backup_files(database: str, timestamp: str | None = None) -> list[str]:
    return list_standard_filenames(database, timestamp)


class BackupManifest(BaseModel):
    """Fields for manifest.json in each backup folder (future implementation)."""

    backup_id: str
    database: str
    backup_kind: BackupKind
    created_at: datetime
    dump_file: str
    sha256: str = ""
    size_bytes: int = 0
    source_role: str
    tool_used: str = "mariadb-dump"
    verified: bool = False
    notes: str = ""


BACKUP_KIND_LABELS = {
    BACKUP_KIND_FULL: "Full logical backup (schema + data) for DR and prod-to-dev sync",
    BACKUP_KIND_SCHEMA_ONLY: "Schema-only (structure) for review and empty shells",
}
