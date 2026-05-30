"""Restore-check planning (dry-run; target _restorecheck_* only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.core import classify_database


class RestoreCheckPlan(BaseModel):
    source_prod: str
    restore_target: str
    backup_directory: str | None = None
    backup_id: str | None = None
    backup_verified: bool = False
    dump_file: str | None = None
    allowed: bool = False
    mode: str = "dry-run"
    planned_commands: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)


def planned_restore_check_name(prod_database: str, *, date: str | None = None) -> str:
    day = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"_restorecheck_{prod_database}_{day}"


def build_restore_check_plan(prod_database: str) -> RestoreCheckPlan:
    """
    Plan a non-destructive restore test into a temporary _restorecheck_* database.

    Does not execute restore. Never targets *_prod or *_dev.
    """
    classification = classify_database(prod_database)
    policy = load_execution_policy()
    target = planned_restore_check_name(prod_database)
    blockers: list[str] = []
    safety = [
        "Restore-check targets _restorecheck_* temp databases only.",
        "Never restore into *_prod or production/shared authority databases.",
    ]

    if not classification.backup_source:
        blockers.append(f"'{prod_database}' is not an approved production backup source.")

    backup_dir = find_latest_backup_directory(policy.backup_root, prod_database)
    backup_verified = False
    backup_id: str | None = None
    dump_file: str | None = None

    if backup_dir is None:
        blockers.append("No on-disk backup found for production source.")
    else:
        verify = verify_backup_artifacts(
            backup_dir,
            database=prod_database,
            backup_kind=BACKUP_KIND_FULL,
        )
        backup_verified = verify.verified
        backup_id = verify.backup_id
        if not verify.verified:
            blockers.append("Latest backup is not verified.")
        manifest_path = backup_dir / "manifest.json"
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            dump_file = data.get("dump_file")

    commands: list[str] = []
    if dump_file and backup_dir is not None:
        artifact = backup_dir / dump_file
        commands = [
            f"# Create temp restore-check database: {target}",
            f"mariadb -e 'CREATE DATABASE IF NOT EXISTS `{target}`;'",
            f"gunzip -c {artifact} | mariadb {target}",
            f"# Validate row counts / spot checks, then DROP DATABASE `{target}`;",
        ]

    allowed = (
        classification.backup_source
        and backup_verified
        and dump_file is not None
        and not blockers
    )

    return RestoreCheckPlan(
        source_prod=prod_database,
        restore_target=target,
        backup_directory=str(backup_dir) if backup_dir else None,
        backup_id=backup_id,
        backup_verified=backup_verified,
        dump_file=dump_file,
        allowed=allowed,
        planned_commands=commands,
        blockers=blockers,
        safety_notes=safety,
    )
