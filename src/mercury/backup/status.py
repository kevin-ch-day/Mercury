"""Aggregate backup protection status for active source databases."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.backup.batch_runner import select_batch_sources
from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.verification import verify_backup_directory
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.database.core import classify_database


class BackupStatusEntry(BaseModel):
    database: str
    role: str
    protection_status: str
    backup_id: str | None = None
    backup_directory: str | None = None
    issues: list[str] = Field(default_factory=list)


class BackupStatusReport(BaseModel):
    backup_root: str
    backup_root_state: str
    source_count: int
    verified_count: int = 0
    missing_count: int = 0
    failed_count: int = 0
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
                )
            )
            missing_count += 1
            continue

        verification = verify_backup_directory(backup_dir, database=database, update_manifest=False)
        status = "verified" if verification.verified else "failed"
        issues = list(verification.issues)
        if warning:
            status = "untrusted root"
            issues = [warning, *issues]

        if status == "verified":
            verified_count += 1
        else:
            failed_count += 1

        entries.append(
            BackupStatusEntry(
                database=database,
                role=role,
                protection_status=status,
                backup_id=verification.backup_id,
                backup_directory=str(backup_dir),
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
        entries=entries,
        warnings=[warning] if warning else [],
    )
