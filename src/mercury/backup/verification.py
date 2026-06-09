"""Backup verification models and artifact checks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.layout import CHECKSUM_FILENAME, MANIFEST_FILENAME
from mercury.backup.manifest import BackupKind, BackupManifest
from mercury.backup.checksum import verify_checksums
from mercury.database.core import classify_database
from mercury.backup.manifest_preview import ManifestPreview, build_manifest_preview
from mercury.core.execution_policy import load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

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
    live_actions_enabled: bool = False


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
    from mercury.backup.on_disk_index import DEMO_BACKUP_RECORDS

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


def _load_manifest(path: Path) -> BackupManifest | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BackupManifest.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return None


def verify_backup_artifacts(
    backup_dir: Path,
    *,
    database: str | None = None,
    backup_kind: BackupKind | None = None,
) -> BackupVerificationResult:
    """
    Verify on-disk backup artifacts: manifest, dumps, checksums, role, and sizes.

    Used after live execution and in tests with synthetic artifacts.
    """
    manifest_path = backup_dir / MANIFEST_FILENAME
    checksum_path = backup_dir / CHECKSUM_FILENAME
    manifest = _load_manifest(manifest_path)

    resolved_db = database or (manifest.database if manifest else backup_dir.name)
    resolved_kind: BackupKind = backup_kind or (
        manifest.backup_kind if manifest else BACKUP_KIND_FULL
    )

    classification = classify_database(resolved_db)
    role_ok = classification.backup_source

    issues: list[str] = []
    manifest_exists = manifest_path.exists() and manifest is not None
    if not manifest_exists:
        issues.append("manifest.json missing or invalid")

    dump_exists = False
    schema_exists = False
    size_ok = True

    dump_name: str | None = None
    schema_name: str | None = None
    if manifest:
        if database and manifest.database != database:
            issues.append(
                f"manifest database '{manifest.database}' does not match requested '{database}'"
            )
        dump_name = manifest.dump_file
        schema_name = manifest.schema_file
        if manifest.live_actions_enabled is False and manifest.dry_run is False:
            issues.append("manifest records live_actions_enabled=false for a non-dry-run backup")

    if resolved_kind == BACKUP_KIND_SCHEMA_ONLY:
        if dump_name:
            artifact = backup_dir / dump_name
            schema_exists = artifact.exists()
            if not schema_exists:
                issues.append(f"Schema dump missing: {dump_name}")
            elif artifact.stat().st_size == 0:
                size_ok = False
                issues.append(f"Schema dump is empty: {dump_name}")
    else:
        if dump_name:
            artifact = backup_dir / dump_name
            dump_exists = artifact.exists()
            if not dump_exists:
                issues.append(f"Full dump missing: {dump_name}")
            elif artifact.stat().st_size == 0:
                size_ok = False
                issues.append(f"Full dump is empty: {dump_name}")
        if schema_name:
            artifact = backup_dir / schema_name
            schema_exists = artifact.exists()
            if not schema_exists:
                issues.append(f"Schema companion missing: {schema_name}")
            elif artifact.stat().st_size == 0:
                size_ok = False
                issues.append(f"Schema companion is empty: {schema_name}")

    checksum_exists = checksum_path.exists()
    checksum_matches = False
    if not checksum_exists:
        issues.append("checksum.sha256 not found")
    else:
        checksum_matches, checksum_issues = verify_checksums(backup_dir, checksum_path)
        issues.extend(checksum_issues)

    if resolved_kind not in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY):
        issues.append(f"Invalid backup kind: {resolved_kind}")

    if not role_ok:
        issues.append(f"Database '{resolved_db}' is not an approved backup source")

    if resolved_kind == BACKUP_KIND_FULL:
        artifacts_ok = dump_exists
        if schema_name:
            if not schema_exists:
                artifacts_ok = False
    else:
        artifacts_ok = schema_exists

    verified = (
        manifest_exists
        and checksum_exists
        and checksum_matches
        and size_ok
        and role_ok
        and resolved_kind in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY)
        and artifacts_ok
        and not any("does not match requested" in issue for issue in issues)
    )

    backup_id = manifest.backup_id if manifest else f"verify-{resolved_db}-{resolved_kind}"

    return BackupVerificationResult(
        backup_id=backup_id,
        database=resolved_db,
        backup_kind=resolved_kind,
        manifest_path=str(manifest_path),
        checked_at=datetime.now(timezone.utc).isoformat(),
        manifest_exists=manifest_exists,
        dump_exists=dump_exists,
        schema_exists=schema_exists,
        checksum_exists=checksum_exists,
        checksum_matches=checksum_matches,
        size_ok=size_ok,
        role_ok=role_ok,
        verified=verified,
        issues=issues,
        preview_only=False,
        live_actions_enabled=(
            manifest.live_actions_enabled
            if manifest
            else load_execution_policy().live_actions_enabled
        ),
    )


def verify_backup_directory(
    backup_dir: Path,
    *,
    database: str | None = None,
    update_manifest: bool = False,
) -> BackupVerificationResult:
    """
    Verify a backup directory on disk; optionally mark manifest verified=true when passing.
    """
    result = verify_backup_artifacts(backup_dir, database=database)
    updated_manifest = False
    if update_manifest and result.verified:
        manifest_path = Path(result.manifest_path)
        manifest = _load_manifest(manifest_path)
        if manifest is not None:
            updated = manifest.model_copy(update={"verified": True})
            manifest_path.write_text(
                json.dumps(updated.model_dump(mode="json"), indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            updated_manifest = True

    from mercury.logging.events import log_verification_result

    log_verification_result(
        database=result.database,
        verified=result.verified,
        issue_count=len(result.issues),
        backup_id=result.backup_id,
        updated_manifest=updated_manifest,
    )
    from mercury.state.ledger import record_backup_verification

    record_backup_verification(result)
    return result
