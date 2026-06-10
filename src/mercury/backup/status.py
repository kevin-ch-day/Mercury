"""Aggregate backup protection status for active source databases."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.batch_runner import select_batch_sources
from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.freshness import (
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    assess_backup_freshness,
    parse_backup_timestamp,
)
from mercury.backup.layout import MANIFEST_FILENAME
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.database.core import classify_database


class BackupStatusEntry(BaseModel):
    database: str
    role: str
    protection_status: str
    backup_id: str | None = None
    backup_directory: str | None = None
    backup_created_at: str | None = None
    freshness: str = FRESHNESS_UNKNOWN
    latest_source_activity_at: str | None = None
    activity_signal: str | None = None
    backup_age: str | None = None
    recommend_full_backup: bool = False
    issues: list[str] = Field(default_factory=list)


class BackupStatusReport(BaseModel):
    backup_root: str
    backup_root_state: str
    source_count: int
    verified_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
    stale_count: int = 0
    unknown_freshness_count: int = 0
    entries: list[BackupStatusEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _role_label(database: str) -> str:
    role = classify_database(database).role.value
    if role == "shared_authority":
        return "shared"
    if role == "production":
        return "prod"
    if role == "development":
        return "dev"
    return role


def _untrusted_root_warning(policy: ExecutionPolicy) -> str | None:
    if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
        return "Repo-local backup root does not count as production protection."
    return None


def _load_backup_created_at(backup_dir: Path) -> str | None:
    manifest_path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    created_at = data.get("created_at")
    return str(created_at) if created_at else None


def build_backup_status_report(
    *,
    live: bool = False,
    selected: list[str] | None = None,
    policy: ExecutionPolicy | None = None,
) -> BackupStatusReport:
    """Inspect the latest backup directory for each active source database."""
    resolved_policy = policy or load_execution_policy()
    sources = select_batch_sources(selected=selected, live=live)
    warning = _untrusted_root_warning(resolved_policy)

    verified_count = 0
    missing_count = 0
    failed_count = 0
    stale_count = 0
    unknown_freshness_count = 0
    entries: list[BackupStatusEntry] = []

    for database in sources:
        role = _role_label(database)
        backup_dir = find_latest_backup_directory(resolved_policy.backup_root, database)
        if backup_dir is None:
            entries.append(
                BackupStatusEntry(
                    database=database,
                    role=role,
                    protection_status="missing",
                    recommend_full_backup=True,
                )
            )
            missing_count += 1
            unknown_freshness_count += 1
            continue

        backup_created_at = _load_backup_created_at(backup_dir)
        freshness = assess_backup_freshness(
            database,
            backup_at=parse_backup_timestamp(backup_created_at),
            live=live,
        )

        verification = verify_backup_artifacts(backup_dir, database=database)
        status = "verified" if verification.verified else "failed"
        issues = list(verification.issues)
        if warning:
            status = "untrusted root"
            issues = [warning, *issues]

        if status == "verified":
            verified_count += 1
        else:
            failed_count += 1

        if freshness.freshness == FRESHNESS_STALE:
            stale_count += 1
        elif freshness.freshness == FRESHNESS_UNKNOWN:
            unknown_freshness_count += 1

        if freshness.recommend_full_backup and status == "verified":
            issues.append(
                "Backup artifacts are verified, but freshness is stale or unknown. "
                "Run full backup before workstation handoff."
            )

        entries.append(
            BackupStatusEntry(
                database=database,
                role=role,
                protection_status=status,
                backup_id=verification.backup_id,
                backup_directory=str(backup_dir),
                backup_created_at=backup_created_at,
                freshness=freshness.freshness,
                latest_source_activity_at=(
                    freshness.latest_source_activity_at.isoformat()
                    if freshness.latest_source_activity_at
                    else None
                ),
                activity_signal=freshness.activity_signal,
                backup_age=freshness.backup_age,
                recommend_full_backup=freshness.recommend_full_backup or status != "verified",
                issues=issues,
            )
        )

    return BackupStatusReport(
        backup_root=str(resolved_policy.backup_root),
        backup_root_state=resolved_policy.backup_root_state(),
        source_count=len(sources),
        verified_count=verified_count,
        missing_count=missing_count,
        failed_count=failed_count,
        stale_count=stale_count,
        unknown_freshness_count=unknown_freshness_count,
        entries=entries,
        warnings=[warning] if warning else [],
    )
