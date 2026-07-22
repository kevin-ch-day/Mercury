"""Restore-check planning (dry-run; target _restorecheck_* only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from mercury.backup.find_latest_backup import (
    BackupSelectionError,
    list_backup_candidates,
    resolve_backup_directory,
)
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.core.runtime import should_probe_database_status
from mercury.database.core import classify_database
from mercury.restore.readiness import TargetCompletenessEntry, build_target_completeness_entry


class RestoreCheckPlan(BaseModel):
    source_prod: str
    restore_target: str
    backup_directory: str | None = None
    backup_id: str | None = None
    backup_verified: bool = False
    dump_file: str | None = None
    allowed: bool = False
    mode: str = "dry-run"
    selection_mode: str = "explicit"  # explicit | artifact_verified_default
    planned_commands: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    candidates: list[dict] = Field(default_factory=list)
    target_completeness: TargetCompletenessEntry | None = None


def planned_restore_check_name(prod_database: str, *, date: str | None = None) -> str:
    day = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"_restorecheck_{prod_database}_{day}"


def build_restore_check_plan(
    prod_database: str,
    *,
    backup_id: str | None = None,
    require_backup_id: bool = False,
    allow_unverified: bool = False,
) -> RestoreCheckPlan:
    """
    Plan a non-destructive restore test into a temporary _restorecheck_* database.

    Does not execute restore. Never targets *_prod or *_dev.

    Non-interactive callers should pass ``backup_id`` (or ``require_backup_id=True``).
    Without an ID, the plan defaults to the latest **artifact-verified** backup and
    lists other candidates — it never silently picks an unverified newer write.
    """
    classification = classify_database(prod_database)
    policy = load_execution_policy()
    target = planned_restore_check_name(prod_database)
    blockers: list[str] = []
    safety = [
        "Restore-check targets _restorecheck_* temp databases only.",
        "Never restore into *_prod or production/shared authority databases.",
        "Restore-check is bound to an exact backup_id; it does not inherit another backup's result.",
    ]
    selection_mode = "explicit" if backup_id else "artifact_verified_default"
    candidates = list_backup_candidates(policy.backup_root, prod_database)

    if not classification.backup_source:
        blockers.append(f"'{prod_database}' is not an approved production backup source.")
    if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
        blockers.append(
            "Backup root is repo-local fallback; configure operator-storage backups before restore-check."
        )
    if require_backup_id and not backup_id:
        blockers.append(
            "Exact --backup-id is required for non-interactive restore-check "
            "(refusing unqualified latest-written selection)."
        )

    backup_dir = None
    backup_verified = False
    resolved_id: str | None = None
    dump_file: str | None = None
    backup_created_at = None

    if not blockers or backup_id:
        try:
            if require_backup_id and not backup_id:
                raise BackupSelectionError("backup_id required")
            backup_dir = resolve_backup_directory(
                policy.backup_root,
                prod_database,
                backup_id=backup_id,
                prefer="artifact_verified",
                allow_unverified=allow_unverified,
            )
        except BackupSelectionError as exc:
            blockers.append(str(exc))
            backup_dir = None

    if backup_dir is not None:
        verify = verify_backup_artifacts(
            backup_dir,
            database=prod_database,
            backup_kind=BACKUP_KIND_FULL,
        )
        backup_verified = verify.verified
        resolved_id = verify.backup_id
        if not verify.verified and not allow_unverified:
            blockers.append(
                f"Selected backup '{resolved_id}' is not artifact-verified. "
                "Pass allow_unverified only with an explicit unsafe override."
            )
        elif not verify.verified and allow_unverified:
            safety.append(
                f"UNSAFE OVERRIDE: restore-checking unverified backup_id '{resolved_id}'."
            )
        if backup_id and resolved_id and backup_id != resolved_id:
            blockers.append(
                f"Requested backup_id '{backup_id}' resolved to unexpected id '{resolved_id}'."
            )
        manifest_path = backup_dir / "manifest.json"
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            dump_file = data.get("dump_file")
            backup_created_at = data.get("created_at")
        if backup_verified and should_probe_database_status():
            from mercury.backup.freshness import backup_stale_handoff_blocker, parse_backup_timestamp

            stale_detail = backup_stale_handoff_blocker(
                prod_database,
                backup_at=parse_backup_timestamp(
                    str(backup_created_at) if backup_created_at else None
                ),
                live=True,
            )
            if stale_detail:
                blockers.append(stale_detail)

    commands: list[str] = []
    if dump_file and backup_dir is not None:
        artifact = backup_dir / dump_file
        commands = [
            f"# Create temp restore-check database: {target}",
            f"# backup_id={resolved_id}",
            f"mariadb -e 'CREATE DATABASE IF NOT EXISTS `{target}`;'",
            f"gunzip -c {artifact} | mariadb {target}",
            f"# Validate row counts / spot checks, then DROP DATABASE `{target}`;",
        ]

    target_completeness: TargetCompletenessEntry | None = None
    if should_probe_database_status():
        target_completeness = build_target_completeness_entry(
            prod_database, backup_id=resolved_id
        )

    allowed = (
        classification.backup_source
        and backup_verified
        and dump_file is not None
        and resolved_id is not None
        and not blockers
    )

    return RestoreCheckPlan(
        source_prod=prod_database,
        restore_target=target,
        backup_directory=str(backup_dir) if backup_dir else None,
        backup_id=resolved_id,
        backup_verified=backup_verified,
        dump_file=dump_file,
        allowed=allowed,
        selection_mode=selection_mode,
        planned_commands=commands,
        blockers=blockers,
        safety_notes=safety,
        candidates=candidates,
        target_completeness=target_completeness,
    )
