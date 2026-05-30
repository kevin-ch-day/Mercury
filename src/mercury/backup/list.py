"""Demo backup inventory from planned manifest previews (not real backups)."""

from pydantic import BaseModel, Field

from mercury.backup.manifest import BackupKind
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
