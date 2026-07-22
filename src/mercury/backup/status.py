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
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.session import try_load_mariadb_config


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
    source_is_empty: bool = False
    restore_check_status: str | None = None
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
    absent_count: int = 0
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


def latest_restore_check_status_by_database() -> dict[str, str]:
    """Latest restore-check outcome per database from the operator ledger."""
    try:
        from mercury.state.ledger import read_operator_database_backup_rows

        rows = read_operator_database_backup_rows()
    except Exception:
        return {}

    latest: dict[str, tuple[str, str]] = {}
    for row in rows:
        database = (row.get("database") or "").strip()
        status = (row.get("restore_check_status") or "").strip()
        stamp = (row.get("timestamp") or "").strip()
        if not database or not status:
            continue
        existing = latest.get(database)
        if existing is None or stamp >= existing[0]:
            latest[database] = (stamp, status)
    return {database: status for database, (_stamp, status) in latest.items()}


def sealed_phase3b_package_note() -> str | None:
    """Observe-only note when the sealed Phase 3B package is present on operator storage."""
    try:
        from mercury.core.usb_mount import resolve_operator_mount
        from mercury.core.storage_roles import CONTROL_DIRNAME

        root = (
            resolve_operator_mount()
            / CONTROL_DIRNAME
            / "phase3b"
            / "20260722T055400Z_phase3b"
        )
        if root.is_dir():
            return (
                "Sealed Phase 3B rehearsal package present "
                "(20260722T055400Z_phase3b). Latest routine backups do not replace it "
                "until restore-check and handoff packaging explicitly promote them."
            )
    except Exception:
        return None
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


def _live_server_database_names(*, live: bool) -> set[str] | None:
    """Return live server DB names when probing; None when offline/unavailable."""
    if not live:
        return None
    try:
        from mercury.backup.live_inventory import fetch_live_server_database_names

        return set(fetch_live_server_database_names())
    except Exception:
        return None


def _source_is_empty_on_server(database: str, *, live: bool, present: bool) -> bool:
    """Return whether a present source has no tables/views, without treating errors as empty."""
    if not live or not present:
        return False
    config = try_load_mariadb_config()
    if config is None:
        return False
    try:
        inspected = inspect_database_on_server(database, config)
    except Exception:
        return False
    return bool(
        inspected.exists_on_server
        and inspected.table_count == 0
        and inspected.view_count == 0
    )


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
    server_names = _live_server_database_names(live=live)
    restore_checks = latest_restore_check_status_by_database()

    verified_count = 0
    missing_count = 0
    failed_count = 0
    stale_count = 0
    unknown_freshness_count = 0
    absent_count = 0
    entries: list[BackupStatusEntry] = []
    warnings: list[str] = [warning] if warning else []
    phase3b_note = sealed_phase3b_package_note()
    if phase3b_note:
        warnings.append(phase3b_note)

    for database in sources:
        role = _role_label(database)
        restore_check_status = restore_checks.get(database)
        backup_dir = find_latest_backup_directory(resolved_policy.backup_root, database)
        absent_on_server = server_names is not None and database not in server_names
        source_is_empty = _source_is_empty_on_server(
            database, live=live, present=not absent_on_server,
        )

        if backup_dir is None:
            if absent_on_server:
                entries.append(
                    BackupStatusEntry(
                        database=database,
                        role=role,
                        protection_status="absent",
                        recommend_full_backup=False,
                        restore_check_status=restore_check_status,
                        issues=[
                            "Database is not present on this MariaDB server; "
                            "backup cannot run until it is created or restored."
                        ],
                    )
                )
                absent_count += 1
                continue
            entries.append(
                BackupStatusEntry(
                    database=database,
                    role=role,
                    protection_status="missing",
                    recommend_full_backup=True,
                    source_is_empty=source_is_empty,
                    restore_check_status=restore_check_status,
                    issues=(
                        [
                            "Live database has no tables or views; create one verified backup "
                            "to preserve the empty schema on the destination."
                        ]
                        if source_is_empty
                        else []
                    ),
                )
            )
            missing_count += 1
            unknown_freshness_count += 1
            continue

        backup_created_at = _load_backup_created_at(backup_dir)
        freshness = assess_backup_freshness(
            database,
            backup_at=parse_backup_timestamp(backup_created_at),
            live=live and not absent_on_server,
            source_is_empty=source_is_empty,
        )

        verification = verify_backup_artifacts(backup_dir, database=database)
        status = "absent" if absent_on_server else ("verified" if verification.verified else "failed")
        issues = list(verification.issues)
        if warning:
            status = "untrusted root"
            issues = [warning, *issues]
        if absent_on_server:
            issues.append(
                "Database is not present on this MariaDB server; "
                "on-disk backup is historical only for this host."
            )

        if status == "absent":
            absent_count += 1
        elif status == "verified":
            verified_count += 1
        else:
            failed_count += 1

        if not absent_on_server and freshness.freshness == FRESHNESS_STALE:
            stale_count += 1
        elif not absent_on_server and freshness.freshness == FRESHNESS_UNKNOWN:
            unknown_freshness_count += 1

        if freshness.recommend_full_backup and status == "verified" and not absent_on_server:
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
                recommend_full_backup=(
                    (freshness.recommend_full_backup or status != "verified")
                    and not absent_on_server
                ),
                source_is_empty=source_is_empty,
                restore_check_status=restore_check_status,
                issues=issues,
            )
        )

    if absent_count:
        warnings.append(
            f"{absent_count} catalog backup source(s) are not present on this MariaDB server "
            "and do not block handoff completeness."
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
        absent_count=absent_count,
        entries=entries,
        warnings=warnings,
    )
