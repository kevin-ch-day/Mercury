"""Prod→dev sync readiness based on artifact-verified on-disk backups and freshness."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.freshness import (
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    assess_backup_freshness,
    parse_backup_timestamp,
)
from mercury.backup.layout import MANIFEST_FILENAME
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.core.scope import is_in_scope
from mercury.database.discovery import discover_for_planning
from mercury.database.prod_dev_pairs import ProdDevPair, build_prod_dev_pairs


class SyncReadinessEntry(BaseModel):
    prod: str
    expected_dev: str
    dev_listed: bool
    project: str | None = None
    latest_backup_dir: str | None = None
    backup_verified: bool = False
    backup_id: str | None = None
    backup_freshness: str | None = None
    ready_for_sync_planning: bool = False
    blockers: list[str] = Field(default_factory=list)


def _load_backup_created_at(backup_dir) -> str | None:
    manifest_path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    created_at = data.get("created_at")
    return str(created_at) if created_at else None


class SyncReadinessReport(BaseModel):
    mode: str
    backup_root: str
    entries: list[SyncReadinessEntry] = Field(default_factory=list)
    ready_count: int = 0
    blocked_count: int = 0


def build_sync_readiness_report(*, live: bool = False) -> SyncReadinessReport:
    """Check prod→dev pairs against verified full backups on disk."""
    from mercury.database.discovery import discover_for_planning

    policy = load_execution_policy()
    inventory = discover_for_planning(live=live)
    mode = "live" if live and inventory.mode == "mariadb_readonly" else "demo"
    names = [entry.name for entry in inventory.entries]
    projects = {entry.name: entry.project for entry in inventory.entries if entry.project}
    pairs = build_prod_dev_pairs(names, projects=projects)

    entries: list[SyncReadinessEntry] = []
    ready_count = 0
    blocked_count = 0

    for pair in pairs:
        if not is_in_scope(pair.expected_dev):
            continue
        blockers: list[str] = []
        if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
            blockers.append(
                "Backup root is repo-local fallback; configure USB-backed backups before sync readiness."
            )
        if not pair.dev_listed:
            blockers.append(f"Dev target missing: {pair.expected_dev}")

        backup_dir = find_latest_backup_directory(policy.backup_root, pair.prod)
        backup_verified = False
        backup_id: str | None = None
        backup_freshness: str | None = None
        latest_dir: str | None = None

        if backup_dir is None:
            blockers.append("No on-disk backup found for production source.")
        else:
            latest_dir = str(backup_dir)
            verify = verify_backup_artifacts(backup_dir, database=pair.prod, backup_kind=BACKUP_KIND_FULL)
            backup_verified = verify.verified
            backup_id = verify.backup_id
            if not verify.verified:
                blockers.append(
                    "Latest backup is not artifact-verified (manifest/checksum/size/role)."
                )
            if verify.backup_kind != BACKUP_KIND_FULL:
                blockers.append("Latest backup is not a verified full backup.")
            elif backup_verified and live and should_probe_database_status():
                freshness = assess_backup_freshness(
                    pair.prod,
                    backup_at=parse_backup_timestamp(_load_backup_created_at(backup_dir)),
                    live=True,
                )
                backup_freshness = freshness.freshness
                if freshness.freshness == FRESHNESS_STALE:
                    blockers.append(
                        "Backup artifacts are artifact-verified but freshness is stale; "
                        "run full backup before prod→dev sync."
                    )
                elif freshness.freshness == FRESHNESS_UNKNOWN:
                    blockers.append(
                        "Backup freshness is unknown; run full backup before prod→dev sync."
                    )

        ready = pair.dev_listed and backup_verified and not blockers
        if ready:
            ready_count += 1
        else:
            blocked_count += 1

        entries.append(
            SyncReadinessEntry(
                prod=pair.prod,
                expected_dev=pair.expected_dev,
                dev_listed=pair.dev_listed,
                project=pair.project,
                latest_backup_dir=latest_dir,
                backup_verified=backup_verified,
                backup_id=backup_id,
                backup_freshness=backup_freshness,
                ready_for_sync_planning=ready,
                blockers=blockers,
            )
        )

    report = SyncReadinessReport(
        mode=mode,
        backup_root=str(policy.backup_root),
        entries=entries,
        ready_count=ready_count,
        blocked_count=blocked_count,
    )
    from mercury.logging.events import log_sync_readiness

    log_sync_readiness(mode=report.mode, ready=report.ready_count, blocked=report.blocked_count)
    return report
