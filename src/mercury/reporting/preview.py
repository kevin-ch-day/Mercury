"""Markdown-style backup report preview (dry-run; no file writes)."""

from pydantic import BaseModel

from mercury.backup.manifest import BackupKind
from mercury.backup.manifest_preview import (
    ManifestPreview,
    ManifestPreviewError,
    build_manifest_preview,
)
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

READINESS_FULL = (
    "Future candidate for disaster recovery and prod-to-dev sync **after** "
    "full verification passes (manifest + checksum + size)."
)
READINESS_SCHEMA_ONLY = (
    "Not sufficient for full disaster recovery or prod-to-dev sync. "
    "Use for schema review and empty database-shell rebuilds only."
)

REPORT_SAFETY_NOTES = [
    "Preview only — no backup files written or verified in seed mode.",
    "A database is not considered protected until verification passes.",
    "Manifest and checksum are required for verified status.",
]


class BackupReportPreview(BaseModel):
    database: str
    project: str | None
    role: str
    backup_kind: BackupKind
    planned_dump_path: str | None = None
    planned_schema_path: str | None = None
    manifest_path: str
    checksum_path: str
    report_path: str
    verification_status: str = "not verified / preview only"
    safety_notes: list[str]
    restore_sync_readiness: str


def build_report_preview(
    database: str,
    kind: BackupKind,
    *,
    date: str | None = None,
    timestamp: str | None = None,
) -> BackupReportPreview:
    """Build report preview; raises ManifestPreviewError for non-backup sources."""
    preview = build_manifest_preview(database, kind, date=date, timestamp=timestamp)
    readiness = (
        READINESS_SCHEMA_ONLY
        if kind == BACKUP_KIND_SCHEMA_ONLY
        else READINESS_FULL
    )
    return BackupReportPreview(
        database=preview.database,
        project=preview.project,
        role=preview.role,
        backup_kind=preview.backup_kind,
        planned_dump_path=preview.planned_dump_file,
        planned_schema_path=preview.planned_schema_file,
        manifest_path=preview.manifest_file,
        checksum_path=preview.checksum_file,
        report_path=preview.report_file,
        safety_notes=list(REPORT_SAFETY_NOTES),
        restore_sync_readiness=readiness,
    )


def format_report_preview_markdown(report: BackupReportPreview) -> str:
    """Render Markdown-style backup report for terminal."""
    lines = [
        "# Mercury Backup Report (preview)",
        "",
        f"- **Database:** {report.database}",
        f"- **Project:** {report.project or 'n/a'}",
        f"- **Role:** {report.role}",
        f"- **Backup kind:** {report.backup_kind}",
        "",
        "## Planned artifacts",
        f"- **Full dump:** {report.planned_dump_path or 'n/a'}",
        f"- **Schema dump:** {report.planned_schema_path or 'n/a'}",
        f"- **Manifest:** {report.manifest_path}",
        f"- **Checksum:** {report.checksum_path}",
        f"- **Report file:** {report.report_path}",
        "",
        "## Verification status",
        report.verification_status,
        "",
        "## Restore / sync readiness",
        report.restore_sync_readiness,
        "",
        "## Safety notes",
    ]
    for note in report.safety_notes:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)
