"""Backup manifest preview (dry-run; does not write files by default)."""

import json
from datetime import datetime, timezone

from pydantic import BaseModel

from mercury.backup.layout import (
    MANIFEST_FILENAME,
    TOOL_FAMILY,
    build_backup_layout,
)
from mercury.database.core import CATALOG_BY_NAME, classify_database, exclusion_reason
from mercury.core.safety import (
    BACKUP_KIND_FULL,
    BACKUP_KIND_SCHEMA_ONLY,
    LIVE_ACTIONS_ENABLED,
)
from mercury.backup.manifest import BackupKind

MANIFEST_PREVIEW_NOTES = {
    BACKUP_KIND_FULL: (
        "Full logical backup: schema + data. A schema-only companion may also be exported."
    ),
    BACKUP_KIND_SCHEMA_ONLY: (
        "Schema-only: tables/views/routines/triggers/events — no table data."
    ),
}


class ManifestPreviewError(ValueError):
    """Database is not eligible for manifest preview as backup source."""


class ManifestPreview(BaseModel):
    backup_id: str
    database: str
    project: str | None = None
    role: str
    backup_kind: BackupKind
    planned_at: str
    planned_directory: str
    planned_dump_file: str | None = None
    planned_schema_file: str | None = None
    manifest_file: str
    checksum_file: str
    report_file: str
    source_role: str
    tool_family: str = TOOL_FAMILY
    dry_run: bool = True
    verified: bool = False
    live_actions_enabled: bool = LIVE_ACTIONS_ENABLED
    notes: str


def build_manifest_preview(
    database: str,
    kind: BackupKind,
    *,
    date: str | None = None,
    timestamp: str | None = None,
) -> ManifestPreview:
    """
    Build a manifest preview for a backup source database.

    Raises ManifestPreviewError if the database is not a backup source.
    """
    classification = classify_database(database)
    if not classification.backup_source:
        reason = exclusion_reason(classification) or "Not a backup source."
        raise ManifestPreviewError(
            f"'{database}' cannot be a backup source ({reason})"
        )

    if kind not in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY):
        raise ValueError(f"Invalid backup_kind: {kind}")

    layout = build_backup_layout(database, date=date, timestamp=timestamp)
    catalog = CATALOG_BY_NAME.get(database)
    instant = datetime.now(timezone.utc).isoformat()

    planned_dump: str | None = None
    planned_schema: str | None = None

    if kind == BACKUP_KIND_SCHEMA_ONLY:
        planned_schema = layout.schema_dump_path()
    else:
        planned_dump = layout.full_dump_path()
        planned_schema = layout.schema_dump_path()

    backup_id = f"preview-{database}-{kind}-{layout.timestamp}"

    return ManifestPreview(
        backup_id=backup_id,
        database=database,
        project=catalog.project if catalog else None,
        role=classification.role.value,
        backup_kind=kind,
        planned_at=instant,
        planned_directory=layout.directory,
        planned_dump_file=planned_dump,
        planned_schema_file=planned_schema,
        manifest_file=layout.manifest_path(),
        checksum_file=layout.checksum_path(),
        report_file=layout.report_path(),
        source_role=classification.role.value,
        notes=MANIFEST_PREVIEW_NOTES[kind],
    )


def format_manifest_preview_json(preview: ManifestPreview) -> str:
    """JSON-like manifest preview for terminal output."""
    return json.dumps(preview.model_dump(), indent=2, default=str)
