"""Backup manifest model (planning; no writes in seed by default)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from mercury.backup.layout import (
    build_backup_layout,
    list_standard_filenames,
    planned_backup_directory,
    planned_dump_filename,
)
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

BackupKind = Literal["full", "schema_only"]

# Re-export layout helpers for backward compatibility
planned_backup_dir = planned_backup_directory


def planned_backup_files(database: str, timestamp: str | None = None) -> list[str]:
    return list_standard_filenames(database, timestamp)


class BackupManifest(BaseModel):
    """Fields for manifest.json in each backup folder."""

    backup_id: str
    database: str
    backup_kind: BackupKind
    created_at: datetime
    dump_file: str
    schema_file: str | None = None
    sha256: str = ""
    schema_sha256: str | None = None
    size_bytes: int = 0
    schema_size_bytes: int | None = None
    source_role: str
    tool_used: str = "mariadb-dump"
    verified: bool = False
    live_actions_enabled: bool = False
    dry_run: bool = True
    notes: str = ""


def build_backup_manifest(
    *,
    database: str,
    backup_kind: BackupKind,
    created_at: datetime,
    source_role: str,
    dump_file: str,
    dump_sha256: str,
    dump_size_bytes: int,
    tool_used: str,
    live_actions_enabled: bool,
    dry_run: bool,
    backup_id: str,
    schema_file: str | None = None,
    schema_sha256: str | None = None,
    schema_size_bytes: int | None = None,
    notes: str = "",
    verified: bool = False,
) -> BackupManifest:
    """Build a manifest record with stable field ordering via the model."""
    return BackupManifest(
        backup_id=backup_id,
        database=database,
        backup_kind=backup_kind,
        created_at=created_at,
        dump_file=dump_file,
        schema_file=schema_file,
        sha256=dump_sha256,
        schema_sha256=schema_sha256,
        size_bytes=dump_size_bytes,
        schema_size_bytes=schema_size_bytes,
        source_role=source_role,
        tool_used=tool_used,
        verified=verified,
        live_actions_enabled=live_actions_enabled,
        dry_run=dry_run,
        notes=notes,
    )


BACKUP_KIND_LABELS = {
    BACKUP_KIND_FULL: "Full logical backup (schema + data) for DR and prod-to-dev sync",
    BACKUP_KIND_SCHEMA_ONLY: "Schema-only (structure) for review and empty shells",
}
