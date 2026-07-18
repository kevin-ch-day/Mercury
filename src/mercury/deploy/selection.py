"""Resolve verified backup artifacts for deployment."""

from __future__ import annotations

import json
from pathlib import Path

from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.core import classify_database
from mercury.deploy.models import DeploymentCandidate
from mercury.backup.batch_runner import resolve_batch_sources


def resolve_deployment_candidates(
    *,
    policy: ExecutionPolicy | None = None,
    databases: list[str] | None = None,
    existing_on_server: set[str] | None = None,
) -> list[DeploymentCandidate]:
    """Latest verified full backup per protected source (operator-storage/catalog scope, not live server)."""
    resolved_policy = policy or load_execution_policy()
    existing = existing_on_server or set()
    source_names = databases or resolve_batch_sources(live=False)
    candidates: list[DeploymentCandidate] = []

    for name in source_names:
        if not classify_database(name).backup_source:
            continue
        backup_dir = find_latest_backup_directory(resolved_policy.backup_root, name)
        if backup_dir is None:
            continue
        verification = verify_backup_artifacts(
            backup_dir,
            database=name,
            backup_kind=BACKUP_KIND_FULL,
        )
        if not verification.verified:
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
