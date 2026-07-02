"""Aggregate Mercury transfer manifest and runbook."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import socket

from pydantic import BaseModel, Field

from mercury.backup.on_disk_index import build_on_disk_backup_list, latest_records_by_database
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import load_execution_policy
from mercury.core.usb_mount import assert_operator_usb_path, resolve_usb_mount
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.reporting.protection import build_protection_report
from mercury.repo import inspect_repositories, load_repo_bundle_settings, load_repo_definitions
from mercury.repo.config import RepoBundleSettings
from mercury.repo.manifest_index import latest_repo_manifest_entries
from mercury.repo.status import RepoStatus
from mercury.sync.readiness import SyncReadinessReport, build_sync_readiness_report
from mercury.state.ledger import record_transfer_bundle_written


class TransferDatabaseEntry(BaseModel):
    database: str
    source_role: str
    verified: bool
    freshness: str | None = None
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
    bundle_path: str | None = None
    repo_manifest_path: str | None = None
    repo_runbook_path: str | None = None
    bundle_verified: bool = False
    bundle_size_bytes: int | None = None
    warning: str | None = None
    error: str | None = None


class TransferBundle(BaseModel):
    generated_at: str
    host: str
    mode: str
    backup_root: str
    required_usb_mount: str
    manifest_dir: str
    runbook_dir: str
    database_entries: list[TransferDatabaseEntry] = Field(default_factory=list)
    repo_entries: list[TransferRepoEntry] = Field(default_factory=list)
    verified_source_count: int = 0
    missing_source_count: int = 0
    failed_source_count: int = 0
    stale_source_count: int = 0
    unknown_freshness_source_count: int = 0
    ready_sync_pairs: int = 0
    blocked_sync_pairs: int = 0
    dirty_repo_names: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    transfer_manifest_path: str
    transfer_runbook_path: str
    latest_transfer_manifest_path: str | None = None
    latest_transfer_runbook_path: str | None = None


def database_package_status_for_bundle(bundle: TransferBundle) -> str:
    from mercury.core.handoff_status import database_bundle_package_status

    return database_bundle_package_status(
        source_count=len(bundle.database_entries),
        verified_count=bundle.verified_source_count,
        missing_count=bundle.missing_source_count,
        failed_count=bundle.failed_source_count,
        stale_count=bundle.stale_source_count,
        unknown_freshness_count=bundle.unknown_freshness_source_count,
    )


def repository_package_status_for_bundle(bundle: TransferBundle) -> str:
    if not bundle.repo_entries:
        return "complete"
    dirty_repos = sum(1 for entry in bundle.repo_entries if entry.dirty and not entry.error)
    repo_errors = sum(1 for entry in bundle.repo_entries if entry.error)
    repo_bundles_verified = all(
        entry.bundle_verified for entry in bundle.repo_entries if not entry.error and entry.bundle_path
    )
    repo_bundles_present = all(
        entry.bundle_path and entry.repo_manifest_path and entry.repo_runbook_path
        for entry in bundle.repo_entries
        if not entry.error
    ) if bundle.repo_entries else False
    if repo_errors or not repo_bundles_present or not repo_bundles_verified:
        return "partial"
    if dirty_repos:
        return "complete with warnings"
    return "complete"


def handoff_status_for_bundle(bundle: TransferBundle) -> str:
    from mercury.core.handoff_status import combine_handoff_status

    return combine_handoff_status(
        database_package_status_for_bundle(bundle),
        repository_package_status_for_bundle(bundle),
    )


def resolve_transfer_live(*, live: bool = False, seed: bool = False) -> bool:
    from mercury.core.runtime import should_probe_database_status

    if seed:
        return False
    if live:
        return True
    return should_probe_database_status()


def _transfer_output_paths(settings: RepoBundleSettings, stamp: str) -> tuple[Path, Path]:
    manifest_path = settings.manifest_dir / f"transfer_manifest_{stamp}.json"
    runbook_path = settings.runbook_dir / f"transfer_runbook_{stamp}.md"
    return manifest_path, runbook_path


def _ensure_usb_path(path: Path) -> None:
    assert_operator_usb_path(path)


def _latest_transfer_artifact(directory: Path, pattern: str) -> Path | None:
    candidates = sorted(directory.glob(pattern))
    return candidates[-1] if candidates else None


def build_transfer_bundle(*, live: bool = False) -> TransferBundle:
    policy = load_execution_policy()
    settings = load_repo_bundle_settings()
    protection = build_protection_report(live=live, probe_database=live)
    readiness = build_sync_readiness_report(live=live)
    backup_list = build_on_disk_backup_list(policy.backup_root)
    latest_by_database = {record.database: record for record in latest_records_by_database(backup_list)}
    repo_statuses = inspect_repositories(load_repo_definitions())
    latest_repo_manifests = latest_repo_manifest_entries(settings.manifest_dir)

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
                freshness=protection.source_freshness.get(database),
                backup_id=record.backup_id if record else None,
                backup_directory=record.directory if record else None,
            )
        )

    repo_entries: list[TransferRepoEntry] = []
    dirty_repo_names: list[str] = []
    for status in repo_statuses:
        manifest_payload = latest_repo_manifests.get(status.key, {})
        warning = None
        if status.dirty:
            dirty_repo_names.append(status.display_name)
            warning = (
                "Repository was dirty at bundle time. Git bundles contain committed history only; "
                "uncommitted changes are not included."
            )
        repo_entries.append(
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
                bundle_path=manifest_payload.get("bundle_path"),
                repo_manifest_path=manifest_payload.get("manifest_path"),
                repo_runbook_path=manifest_payload.get("runbook_path"),
                bundle_verified=bool(manifest_payload.get("bundle_verified", False)),
                bundle_size_bytes=manifest_payload.get("bundle_size_bytes"),
                warning=warning,
                error=status.error,
            )
        )

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    manifest_path, runbook_path = _transfer_output_paths(settings, stamp)
    latest_transfer_manifest = _latest_transfer_artifact(settings.manifest_dir, "transfer_manifest_*.json")
    latest_transfer_runbook = _latest_transfer_artifact(settings.runbook_dir, "transfer_runbook_*.md")
    warnings: list[str] = []
    if dirty_repo_names:
        warnings.append(
            "Dirty repos not fully captured by Git bundles: " + ", ".join(dirty_repo_names)
        )
    return TransferBundle(
        generated_at=now.isoformat(),
        host=socket.gethostname(),
        mode="live" if live else "seed",
        backup_root=str(policy.backup_root),
        required_usb_mount=str(resolve_usb_mount()),
        manifest_dir=str(settings.manifest_dir),
        runbook_dir=str(settings.runbook_dir),
        database_entries=database_entries,
        repo_entries=repo_entries,
        verified_source_count=protection.verified_source_count,
        missing_source_count=protection.missing_source_count,
        failed_source_count=protection.failed_source_count,
        stale_source_count=protection.stale_source_count,
        unknown_freshness_source_count=protection.unknown_freshness_source_count,
        ready_sync_pairs=readiness.ready_count,
        blocked_sync_pairs=readiness.blocked_count,
        dirty_repo_names=dirty_repo_names,
        warnings=warnings,
        transfer_manifest_path=str(manifest_path),
        transfer_runbook_path=str(runbook_path),
        latest_transfer_manifest_path=str(latest_transfer_manifest) if latest_transfer_manifest else None,
        latest_transfer_runbook_path=str(latest_transfer_runbook) if latest_transfer_runbook else None,
    )


def _runbook_text(bundle: TransferBundle) -> str:
    db_package = database_package_status_for_bundle(bundle)
    repo_package = repository_package_status_for_bundle(bundle)
    handoff_status = handoff_status_for_bundle(bundle)
    lines = [
        "# Mercury transfer runbook",
        "",
        f"Generated: {bundle.generated_at}",
        f"Host: {bundle.host}",
        f"Mode: {bundle.mode}",
        f"Handoff readiness: {handoff_status}",
        f"Database package: {db_package}",
        f"Repository package: {repo_package}",
        f"Verified sources: {bundle.verified_source_count} of {len(bundle.database_entries)}",
        f"Stale sources: {bundle.stale_source_count}",
        f"Unknown freshness: {bundle.unknown_freshness_source_count}",
        f"Missing sources: {bundle.missing_source_count}",
        f"USB mount: {bundle.required_usb_mount}",
        f"Database backup root: {bundle.backup_root}",
        f"Manifest dir: {bundle.manifest_dir}",
        f"Runbook dir: {bundle.runbook_dir}",
        "",
        "Receiving workstation checklist:",
        "1. Mount this USB and confirm mercury_backups, mercury_manifests, and mercury_runbooks are present.",
        "2. Install Mercury and run ./run.sh config init on the receiving host.",
        "3. Run ./run.sh deploy system to import verified USB database backups.",
        "4. Run ./run.sh deploy repos --from-usb for repository bundles.",
        "5. Open the latest database_transfer_runbook and per-database restore notes before any live restore.",
        "6. Run restore-check drills against verified backups before relying on production paths.",
        "",
        "Database restore inputs:",
    ]
    for entry in bundle.database_entries:
        lines.extend(
            [
                f"- {entry.database}",
                f"  role: {entry.source_role}",
                f"  verified: {entry.verified}",
                f"  freshness: {entry.freshness or 'unknown'}",
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
                f"  bundle: {entry.bundle_path or 'missing'}",
                f"  bundle_verified: {entry.bundle_verified}",
            ]
        )
        if entry.warning:
            lines.append(f"  warning: {entry.warning}")
    lines.extend(
        [
            "",
            f"Prod-to-dev sync readiness: {bundle.ready_sync_pairs} ready, {bundle.blocked_sync_pairs} blocked",
            "Actual sync: deferred",
            "",
            "Notes:",
            "- Database restore-check uses temporary _restorecheck_* databases only.",
            "- Prod-to-dev sync must never target production databases.",
            "- Git bundles include committed history only, not dirty tracked changes or untracked files.",
            "",
        ]
    )
    if bundle.warnings:
        lines.extend(["Warnings:"])
        lines.extend([f"- {warning}" for warning in bundle.warnings])
        lines.append("")
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
    record_transfer_bundle_written(bundle)
    return bundle
