"""Resolve verified backup artifacts for deployment."""

from __future__ import annotations

import json
from pathlib import Path

from mercury.backup.find_latest_backup import (
    BackupSelectionError,
    resolve_backup_directory,
)
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.core import classify_database
from mercury.deploy.models import DeploymentCandidate
from mercury.backup.batch_runner import resolve_batch_sources, resolve_development_backup_sources


def resolve_deployment_candidates(
    *,
    policy: ExecutionPolicy | None = None,
    databases: list[str] | None = None,
    backup_ids: dict[str, str] | None = None,
    require_backup_ids: bool = False,
    existing_on_server: set[str] | None = None,
    allow_development_deploy: bool = False,
) -> list[DeploymentCandidate]:
    """Resolve verified backups by exact ID when provided, else latest artifact-verified.

    Destination rehearsal/packaging must pass ``backup_ids`` (or ``require_backup_ids=True``)
    so an unqualified “latest” cannot replace a pinned plan after creation.
    """
    resolved_policy = policy or load_execution_policy()
    existing = existing_on_server or set()
    pinned = backup_ids or {}
    development_sources = (
        set(resolve_development_backup_sources(live=False)) if allow_development_deploy else set()
    )
    source_names = databases or (
        sorted(development_sources) if allow_development_deploy else resolve_batch_sources(live=False)
    )
    candidates: list[DeploymentCandidate] = []

    for name in source_names:
        classification = classify_database(name)
        if not classification.backup_source and not (allow_development_deploy and name in development_sources):
            continue
        requested_id = pinned.get(name)
        if require_backup_ids and not requested_id:
            continue
        try:
            backup_dir = resolve_backup_directory(
                resolved_policy.backup_root,
                name,
                backup_id=requested_id,
                prefer="artifact_verified",
            )
        except BackupSelectionError:
            continue
        verification = verify_backup_artifacts(
            backup_dir,
            database=name,
            backup_kind=BACKUP_KIND_FULL,
            allow_development_backup=allow_development_deploy,
        )
        if not verification.verified:
            continue
        if requested_id and verification.backup_id and requested_id != verification.backup_id:
            continue
        manifest_path = backup_dir / "manifest.json"
        manifest_data: dict = {}
        if manifest_path.exists():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest_data = {}
        dump_name = manifest_data.get("dump_file") or verification.backup_id
        dump_path = backup_dir / str(dump_name) if dump_name else backup_dir
        candidates.append(
            DeploymentCandidate(
                source_database=name,
                target_database=name,
                backup_directory=str(backup_dir),
                backup_id=verification.backup_id,
                dump_path=str(dump_path),
                manifest_path=str(manifest_path),
                checksum_path=str(backup_dir / "checksum.sha256"),
                size_bytes=int(manifest_data.get("size_bytes") or 0),
                verified=True,
                created_at=str(manifest_data.get("created_at") or ""),
                exists_on_server=name in existing,
            )
        )
    return candidates
