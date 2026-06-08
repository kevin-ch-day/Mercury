"""Demo backup inventory and on-disk backup listing."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.layout import MANIFEST_FILENAME
from mercury.backup.manifest import BackupKind, BackupManifest
from mercury.backup.manifest_preview import ManifestPreview, build_manifest_preview
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

# Demo planned records for M4.5 list preview
DEMO_BACKUP_RECORDS: list[tuple[str, BackupKind]] = [
    ("erebus_threat_intel_prod", BACKUP_KIND_FULL),
    ("erebus_threat_intel_prod", BACKUP_KIND_SCHEMA_ONLY),
    ("android_permission_intel", BACKUP_KIND_FULL),
    ("scytaledroid_core_prod", BACKUP_KIND_SCHEMA_ONLY),
]


class DemoBackupRecord(BaseModel):
    label: str = "demo planned record"
    database: str
    backup_kind: BackupKind
    backup_id: str
    planned_directory: str
    planned_dump_file: str | None = None
    planned_schema_file: str | None = None
    verified: bool = False
    preview_only: bool = True


class DemoBackupList(BaseModel):
    mode: str = "demo"
    records: list[DemoBackupRecord] = Field(default_factory=list)
    note: str = "These are demo planned records from manifest previews, not real backups."


def build_demo_backup_list(
    *,
    date: str | None = None,
    timestamp: str | None = None,
) -> DemoBackupList:
    records: list[DemoBackupRecord] = []
    for database, kind in DEMO_BACKUP_RECORDS:
        preview = build_manifest_preview(database, kind, date=date, timestamp=timestamp)
        records.append(_record_from_preview(preview))
    return DemoBackupList(records=records)


def _record_from_preview(preview: ManifestPreview) -> DemoBackupRecord:
    return DemoBackupRecord(
        database=preview.database,
        backup_kind=preview.backup_kind,
        backup_id=preview.backup_id,
        planned_directory=preview.planned_directory,
        planned_dump_file=preview.planned_dump_file,
        planned_schema_file=preview.planned_schema_file,
        verified=False,
        preview_only=True,
    )


class OnDiskBackupRecord(BaseModel):
    database: str
    backup_kind: BackupKind
    backup_id: str
    directory: str
    dump_file: str | None = None
    schema_file: str | None = None
    verified: bool = False
    created_at: str | None = None


class OnDiskBackupList(BaseModel):
    mode: str = "on-disk"
    backup_root: str
    records: list[OnDiskBackupRecord] = Field(default_factory=list)
    note: str = "Backups discovered from manifest.json files under backup_root."


def latest_records_by_database(backup_list: OnDiskBackupList) -> list[OnDiskBackupRecord]:
    """Return the latest tracked backup record for each database."""
    latest: dict[str, OnDiskBackupRecord] = {}
    for record in backup_list.records:
        latest.setdefault(record.database, record)
    return list(latest.values())


def _load_manifest(path: Path) -> BackupManifest | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BackupManifest.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return None


def build_on_disk_backup_list(backup_root: Path) -> OnDiskBackupList:
    """Scan backup_root for manifest.json files and build a sorted inventory."""
    records: list[OnDiskBackupRecord] = []
    root = backup_root.expanduser()
    if not root.is_dir():
        return OnDiskBackupList(backup_root=str(root), records=[])

    try:
        manifest_paths = sorted(root.glob("*/*/manifest.json"))
    except OSError:
        return OnDiskBackupList(backup_root=str(root), records=[])

    for manifest_path in manifest_paths:
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            continue
        records.append(
            OnDiskBackupRecord(
                database=manifest.database,
                backup_kind=manifest.backup_kind,
                backup_id=manifest.backup_id,
                directory=str(manifest_path.parent),
                dump_file=manifest.dump_file,
                schema_file=manifest.schema_file,
                verified=manifest.verified,
                created_at=manifest.created_at.isoformat(),
            )
        )

    records.sort(key=lambda record: record.created_at or "", reverse=True)
    return OnDiskBackupList(backup_root=str(root.resolve()), records=records)
