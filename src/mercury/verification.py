"""Backup verification models and demo results (M4.5 — no live verification yet)."""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup_manifest import BackupKind
from mercury.database.core import classify_database
from mercury.manifest_preview import ManifestPreview, build_manifest_preview
from mercury.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY, LIVE_ACTIONS_ENABLED

VERIFICATION_FUTURE_CHECKS: list[str] = [
    "manifest.json exists",
    "dump file exists",
    "schema file exists when expected",
    "checksum.sha256 exists",
    "sha256 matches dump/schema files",
    "size_bytes > 0",
    "database role is backup source",
    "backup kind is full or schema_only",
    "live_actions_enabled policy recorded",
]

VERIFICATION_SEED_STATUS: list[str] = [
    "dry-run only",
    "no files verified",
    "no database contacted",
]


class BackupVerificationResult(BaseModel):
    """Future/live verification outcome for one backup artifact set."""

    backup_id: str
    database: str
    backup_kind: BackupKind
    manifest_path: str
    checked_at: str | None = None
    manifest_exists: bool = False
    dump_exists: bool = False
    schema_exists: bool = False
    checksum_exists: bool = False
    checksum_matches: bool = False
    size_ok: bool = False
    role_ok: bool = False
    verified: bool = False
    issues: list[str] = Field(default_factory=list)
    preview_only: bool = True
    live_actions_enabled: bool = LIVE_ACTIONS_ENABLED


class VerificationPlan(BaseModel):
    mode: str = "dry-run"
    future_checks: list[str] = Field(default_factory=lambda: list(VERIFICATION_FUTURE_CHECKS))
    seed_status: list[str] = Field(default_factory=lambda: list(VERIFICATION_SEED_STATUS))
    demo_results: list[BackupVerificationResult] = Field(default_factory=list)


def _path_exists_optional(path: str) -> bool:
    """Optional light check: true only if path exists on disk (seed usually false)."""
    if not path:
        return False
    return Path(path).exists()


def build_demo_verification_result(
    preview: ManifestPreview,
    *,
    date: str | None = None,
    timestamp: str | None = None,
) -> BackupVerificationResult:
    """
    Build a verification result from manifest preview data.

    In seed/demo mode all file checks default false unless files happen to exist.
    verified is always false in demo.
    """
    classification = classify_database(preview.database)
    role_ok = classification.backup_source

    manifest_exists = _path_exists_optional(preview.manifest_file)
    dump_exists = _path_exists_optional(preview.planned_dump_file or "")
    schema_exists = _path_exists_optional(preview.planned_schema_file or "")
    checksum_exists = _path_exists_optional(preview.checksum_file)

    issues: list[str] = ["Seed/demo: verification not executed against real backup files."]
    if not manifest_exists:
        issues.append("manifest.json not found on disk (expected in seed).")
    if preview.backup_kind == BACKUP_KIND_FULL and not dump_exists:
        issues.append("Full dump file not found on disk.")
    if preview.planned_schema_file and not schema_exists:
        issues.append("Schema file not found on disk.")
    if not checksum_exists:
        issues.append("checksum.sha256 not found on disk.")
    issues.append("checksum_matches not computed in seed mode.")
    issues.append("size_bytes not checked in seed mode.")

    expect_schema = preview.planned_schema_file is not None

    return BackupVerificationResult(
        backup_id=preview.backup_id,
        database=preview.database,
        backup_kind=preview.backup_kind,
        manifest_path=preview.manifest_file,
        checked_at=datetime.now(timezone.utc).isoformat(),
        manifest_exists=manifest_exists,
        dump_exists=dump_exists,
        schema_exists=schema_exists if expect_schema else False,
        checksum_exists=checksum_exists,
        checksum_matches=False,
        size_ok=False,
        role_ok=role_ok,
        verified=False,
        issues=issues,
        preview_only=True,
    )


def build_verification_plan_demo(
    *,
    date: str | None = None,
    timestamp: str | None = None,
) -> VerificationPlan:
    """Demo verification plan with preview results for sample backup records."""
    from mercury.backup_list import DEMO_BACKUP_RECORDS

    results: list[BackupVerificationResult] = []
    for database, kind in DEMO_BACKUP_RECORDS:
        preview = build_manifest_preview(database, kind, date=date, timestamp=timestamp)
        results.append(
            build_demo_verification_result(preview, date=date, timestamp=timestamp)
        )

    return VerificationPlan(demo_results=results)


def apply_verification_success(result: BackupVerificationResult) -> BackupVerificationResult:
    """Helper for tests: simulate a fully passing verification."""
    return result.model_copy(
        update={
            "manifest_exists": True,
            "dump_exists": True,
            "schema_exists": True,
            "checksum_exists": True,
            "checksum_matches": True,
            "size_ok": True,
            "role_ok": True,
            "verified": True,
            "issues": [],
            "preview_only": False,
        }
    )
