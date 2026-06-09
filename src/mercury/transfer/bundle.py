"""Aggregate Mercury transfer manifest and runbook."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.on_disk_index import build_on_disk_backup_list, latest_records_by_database
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import REQUIRED_BACKUP_MOUNT, load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.reporting.protection import build_protection_report
from mercury.repo import inspect_repositories, load_repo_bundle_settings, load_repo_definitions
from mercury.repo.config import RepoBundleSettings
from mercury.repo.status import RepoStatus
from mercury.sync.readiness import SyncReadinessReport, build_sync_readiness_report


class TransferDatabaseEntry(BaseModel):
    database: str
    source_role: str
    verified: bool
    backup_id: str | None = None
    backup_directory: str | None = None


class TransferRepoEntry(BaseModel):
    repo_key: str
    repo_name: str
    repo_path: str
    branch: str
    commit: str
    remote_url: str
    dirty: bool
    untracked_count: int
    ahead_count: int | None = None
    behind_count: int | None = None
    error: str | None = None


class TransferBundle(BaseModel):
    generated_at: str
    mode: str
    backup_root: str
    manifest_dir: str
    runbook_dir: str
    database_entries: list[TransferDatabaseEntry] = Field(default_factory=list)
    repo_entries: list[TransferRepoEntry] = Field(default_factory=list)
    ready_sync_pairs: int = 0
    blocked_sync_pairs: int = 0
    transfer_manifest_path: str
    transfer_runbook_path: str


def _transfer_output_paths(settings: RepoBundleSettings, stamp: str) -> tuple[Path, Path]:
    manifest_path = settings.manifest_dir / f"transfer_manifest_{stamp}.json"
    runbook_path = settings.runbook_dir / f"transfer_runbook_{stamp}.md"
    return manifest_path, runbook_path


def _ensure_usb_path(path: Path) -> None:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REQUIRED_BACKUP_MOUNT)
    except ValueError as exc:
        raise ValueError(f"path is not under {REQUIRED_BACKUP_MOUNT}: {resolved}") from exc
    if not REQUIRED_BACKUP_MOUNT.is_mount():
        raise ValueError(f"required USB mount is not active: {REQUIRED_BACKUP_MOUNT}")


def build_transfer_bundle(*, live: bool = False) -> TransferBundle:
    policy = load_execution_policy()
    settings = load_repo_bundle_settings()
    protection = build_protection_report(live=live, probe_database=live)
    readiness = build_sync_readiness_report(live=live)
    backup_list = build_on_disk_backup_list(policy.backup_root)
    latest_by_database = {record.database: record for record in latest_records_by_database(backup_list)}
    repo_statuses = inspect_repositories(load_repo_definitions())

    database_entries: list[TransferDatabaseEntry] = []
    for database in protection.protected:
        record = latest_by_database.get(database)
        source_role = (
            "shared authority" if database in protection.shared_authority else "production source"
        )
        verified = False
        if record and record.directory:
            result = verify_backup_artifacts(Path(record.directory), database=database)
            verified = result.verified and result.backup_kind == BACKUP_KIND_FULL
        database_entries.append(
            TransferDatabaseEntry(
                database=database,
                source_role=source_role,
                verified=verified,
                backup_id=record.backup_id if record else None,
                backup_directory=record.directory if record else None,
            )
        )

    repo_entries = [
        TransferRepoEntry(
            repo_key=status.key,
            repo_name=status.display_name,
            repo_path=str(status.path),
            branch=status.branch,
            commit=status.commit,
            remote_url=status.remote_url,
            dirty=status.dirty,
            untracked_count=status.untracked_count,
            ahead_count=status.ahead_count,
            behind_count=status.behind_count,
            error=status.error,
        )
        for status in repo_statuses
    ]

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    manifest_path, runbook_path = _transfer_output_paths(settings, stamp)
    return TransferBundle(
        generated_at=now.isoformat(),
        mode="live" if live else "seed",
        backup_root=str(policy.backup_root),
        manifest_dir=str(settings.manifest_dir),
        runbook_dir=str(settings.runbook_dir),
        database_entries=database_entries,
        repo_entries=repo_entries,
        ready_sync_pairs=readiness.ready_count,
        blocked_sync_pairs=readiness.blocked_count,
        transfer_manifest_path=str(manifest_path),
        transfer_runbook_path=str(runbook_path),
    )


def _runbook_text(bundle: TransferBundle) -> str:
    lines = [
        "# Mercury transfer runbook",
        "",
        f"Generated: {bundle.generated_at}",
        f"Mode: {bundle.mode}",
        f"Database backup root: {bundle.backup_root}",
        "",
        "Database restore inputs:",
    ]
    for entry in bundle.database_entries:
        lines.extend(
            [
                f"- {entry.database}",
                f"  role: {entry.source_role}",
                f"  verified: {entry.verified}",
                f"  backup_id: {entry.backup_id or 'missing'}",
                f"  backup_directory: {entry.backup_directory or 'missing'}",
            ]
        )
    lines.extend(
        [
            "",
            "Repository transfer inputs:",
        ]
    )
    for entry in bundle.repo_entries:
        state = "dirty" if entry.dirty else "clean"
        if entry.error:
            state = f"error ({entry.error})"
        lines.extend(
            [
                f"- {entry.repo_name}",
                f"  path: {entry.repo_path}",
                f"  branch: {entry.branch}",
                f"  commit: {entry.commit}",
                f"  remote: {entry.remote_url}",
                f"  worktree: {state}",
            ]
        )
    lines.extend(
        [
            "",
            f"Prod-to-dev sync readiness: {bundle.ready_sync_pairs} ready, {bundle.blocked_sync_pairs} blocked",
            "",
            "Notes:",
            "- Database restore-check uses temporary _restorecheck_* databases only.",
            "- Prod-to-dev sync must never target production databases.",
            "- Git bundles include committed history only, not dirty tracked changes or untracked files.",
            "",
        ]
    )
    return "\n".join(lines)


def write_transfer_bundle(bundle: TransferBundle) -> TransferBundle:
    manifest_path = Path(bundle.transfer_manifest_path)
    runbook_path = Path(bundle.transfer_runbook_path)
    _ensure_usb_path(manifest_path)
    _ensure_usb_path(runbook_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    runbook_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
    runbook_path.write_text(_runbook_text(bundle), encoding="utf-8")
    return bundle
