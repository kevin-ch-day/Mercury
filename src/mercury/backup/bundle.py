"""Aggregate database backup manifest and restore runbooks."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.status import BackupStatusEntry, build_backup_status_report
from mercury.core.execution_policy import REQUIRED_BACKUP_MOUNT
from mercury.repo.config import load_repo_bundle_settings


class DatabaseBundleEntry(BaseModel):
    database: str
    role: str
    protection_status: str
    backup_id: str | None = None
    backup_directory: str | None = None
    planned_manifest_path: Path
    planned_runbook_path: Path
    issues: list[str] = Field(default_factory=list)


class DatabaseBundlePlan(BaseModel):
    generated_at: str
    backup_root: Path
    manifest_dir: Path
    runbook_dir: Path
    planned_index_manifest_path: Path
    planned_index_runbook_path: Path
    source_count: int
    verified_count: int
    missing_count: int
    failed_count: int
    entries: list[DatabaseBundleEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _slug(text: str) -> str:
    lowered = text.strip().lower()
    chars = [c if c.isalnum() else "_" for c in lowered]
    while "__" in (value := "".join(chars)):
        chars = list(value.replace("__", "_"))
    return "".join(chars).strip("_") or "database"


def _artifact_paths(database: str, stamp_date: str, stamp: str) -> tuple[Path, Path]:
    settings = load_repo_bundle_settings()
    slug = _slug(database)
    manifest_path = settings.manifest_dir / stamp_date / f"{slug}_{stamp}.db_manifest.json"
    runbook_path = settings.runbook_dir / stamp_date / f"{slug}_{stamp}.restore.md"
    return manifest_path, runbook_path


def _index_paths(stamp_date: str, stamp: str) -> tuple[Path, Path]:
    settings = load_repo_bundle_settings()
    manifest_path = settings.manifest_dir / stamp_date / f"database_transfer_manifest_{stamp}.json"
    runbook_path = settings.runbook_dir / stamp_date / f"database_transfer_runbook_{stamp}.md"
    return manifest_path, runbook_path


def build_database_bundle_plan(
    *,
    live: bool = False,
    selected: list[str] | None = None,
) -> DatabaseBundlePlan:
    report = build_backup_status_report(live=live, selected=selected)
    settings = load_repo_bundle_settings()
    now = datetime.now(timezone.utc)
    stamp_date = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y%m%d_%H%M%S")
    index_manifest_path, index_runbook_path = _index_paths(stamp_date, stamp)

    entries: list[DatabaseBundleEntry] = []
    for entry in report.entries:
        manifest_path, runbook_path = _artifact_paths(entry.database, stamp_date, stamp)
        entries.append(
            DatabaseBundleEntry(
                database=entry.database,
                role=entry.role,
                protection_status=entry.protection_status,
                backup_id=entry.backup_id,
                backup_directory=entry.backup_directory,
                planned_manifest_path=manifest_path,
                planned_runbook_path=runbook_path,
                issues=list(entry.issues),
            )
        )

    return DatabaseBundlePlan(
        generated_at=now.isoformat(),
        backup_root=Path(report.backup_root),
        manifest_dir=settings.manifest_dir,
        runbook_dir=settings.runbook_dir,
        planned_index_manifest_path=index_manifest_path,
        planned_index_runbook_path=index_runbook_path,
        source_count=report.source_count,
        verified_count=report.verified_count,
        missing_count=report.missing_count,
        failed_count=report.failed_count,
        entries=entries,
        warnings=list(report.warnings),
    )


def _ensure_usb_path(path: Path) -> None:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REQUIRED_BACKUP_MOUNT)
    except ValueError as exc:
        raise ValueError(f"path is not under {REQUIRED_BACKUP_MOUNT}: {resolved}") from exc
    if not REQUIRED_BACKUP_MOUNT.is_mount():
        raise ValueError(f"required USB mount is not active: {REQUIRED_BACKUP_MOUNT}")


def _entry_manifest_payload(plan: DatabaseBundlePlan, entry: DatabaseBundleEntry) -> dict[str, object]:
    return {
        "generated_at": plan.generated_at,
        "database": entry.database,
        "role": entry.role,
        "protection_status": entry.protection_status,
        "backup_id": entry.backup_id,
        "backup_directory": entry.backup_directory,
        "manifest_path": str(entry.planned_manifest_path),
        "runbook_path": str(entry.planned_runbook_path),
        "issues": entry.issues,
    }


def _index_manifest_payload(plan: DatabaseBundlePlan) -> dict[str, object]:
    return {
        "generated_at": plan.generated_at,
        "backup_root": str(plan.backup_root),
        "manifest_dir": str(plan.manifest_dir),
        "runbook_dir": str(plan.runbook_dir),
        "source_count": plan.source_count,
        "verified_count": plan.verified_count,
        "missing_count": plan.missing_count,
        "failed_count": plan.failed_count,
        "warnings": plan.warnings,
        "databases": [
            {
                "database": entry.database,
                "role": entry.role,
                "protection_status": entry.protection_status,
                "backup_id": entry.backup_id,
                "backup_directory": entry.backup_directory,
                "manifest_path": str(entry.planned_manifest_path),
                "runbook_path": str(entry.planned_runbook_path),
                "issues": entry.issues,
            }
            for entry in plan.entries
        ],
    }


def _entry_runbook_text(entry: DatabaseBundleEntry) -> str:
    lines = [
        f"# Restore {entry.database}",
        "",
        f"Role: {entry.role}",
        f"Protection status: {entry.protection_status}",
        f"Backup ID: {entry.backup_id or 'missing'}",
        f"Backup directory: {entry.backup_directory or 'missing'}",
        "",
        "Recommended sequence:",
    ]
    if entry.backup_directory:
        lines.extend(
            [
                f"1. mercury backup verify --db {entry.database} --path {entry.backup_directory}",
                f"2. mercury restore-check plan --db {entry.database}",
                f"3. mercury restore-check run --db {entry.database} --execute",
            ]
        )
    else:
        lines.append("1. No backup directory is available yet for this database.")
    lines.extend(
        [
            "",
            "Notes:",
            "- Restore-check uses temporary _restorecheck_* databases only.",
            "- Mercury never restores into *_prod or shared authority production data.",
        ]
    )
    if entry.issues:
        lines.extend(["", "Current issues:"])
        lines.extend(f"- {issue}" for issue in entry.issues)
    lines.append("")
    return "\n".join(lines)


def _index_runbook_text(plan: DatabaseBundlePlan) -> str:
    lines = [
        "# Mercury database transfer runbook",
        "",
        f"Generated: {plan.generated_at}",
        f"Backup root: {plan.backup_root}",
        f"Source databases: {plan.source_count}",
        f"Verified: {plan.verified_count}",
        f"Missing: {plan.missing_count}",
        f"Failed: {plan.failed_count}",
        "",
        "Databases:",
    ]
    for entry in plan.entries:
        lines.extend(
            [
                f"- {entry.database}",
                f"  role: {entry.role}",
                f"  protection_status: {entry.protection_status}",
                f"  backup_id: {entry.backup_id or 'missing'}",
                f"  backup_directory: {entry.backup_directory or 'missing'}",
                f"  manifest: {entry.planned_manifest_path}",
                f"  restore note: {entry.planned_runbook_path}",
            ]
        )
    lines.extend(
        [
            "",
            "Notes:",
            "- A database is not protected until verification passes.",
            "- Restore-check uses disposable _restorecheck_* databases only.",
            "- Prod-to-dev sync requires verified full backups for production sync sources.",
            "",
        ]
    )
    if plan.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in plan.warnings)
        lines.append("")
    return "\n".join(lines)


def write_database_bundle_plan(plan: DatabaseBundlePlan) -> DatabaseBundlePlan:
    _ensure_usb_path(plan.manifest_dir)
    _ensure_usb_path(plan.runbook_dir)
    _ensure_usb_path(plan.planned_index_manifest_path)
    _ensure_usb_path(plan.planned_index_runbook_path)

    for entry in plan.entries:
        entry.planned_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        entry.planned_runbook_path.parent.mkdir(parents=True, exist_ok=True)
        entry.planned_manifest_path.write_text(
            json.dumps(_entry_manifest_payload(plan, entry), indent=2) + "\n",
            encoding="utf-8",
        )
        entry.planned_runbook_path.write_text(
            _entry_runbook_text(entry),
            encoding="utf-8",
        )

    plan.planned_index_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.planned_index_runbook_path.parent.mkdir(parents=True, exist_ok=True)
    plan.planned_index_manifest_path.write_text(
        json.dumps(_index_manifest_payload(plan), indent=2) + "\n",
        encoding="utf-8",
    )
    plan.planned_index_runbook_path.write_text(
        _index_runbook_text(plan),
        encoding="utf-8",
    )
    return plan
