"""Append-only portable operation ledger for Mercury."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mercury.core.execution_policy import REQUIRED_BACKUP_MOUNT, ExecutionPolicy, load_execution_policy
from mercury.core.paths import DATA_DIR

STATE_DIRNAME = "mercury_state"
OPERATIONS_JSONL = "operations.jsonl"
DATABASE_BACKUPS_CSV = "database_backups.csv"
REPO_BUNDLES_CSV = "repo_bundles.csv"
TRANSFER_PACKAGES_CSV = "transfer_packages.csv"
SYNC_EVENTS_CSV = "sync_events.csv"

DATABASE_BACKUP_FIELDS = [
    "timestamp",
    "database",
    "role",
    "event",
    "backup_kind",
    "backup_id",
    "backup_path",
    "dump_file",
    "schema_file",
    "size_bytes",
    "verified",
    "restore_check_status",
    "warnings",
]

REPO_BUNDLE_FIELDS = [
    "timestamp",
    "repo_name",
    "path",
    "branch",
    "commit",
    "remote",
    "dirty",
    "untracked_count",
    "bundle_path",
    "bundle_verified",
    "bundle_size_bytes",
    "warnings",
]

TRANSFER_PACKAGE_FIELDS = [
    "timestamp",
    "manifest_path",
    "runbook_path",
    "database_sources",
    "verified_sources",
    "repo_count",
    "dirty_repo_count",
    "sync_ready",
    "sync_blocked",
    "actual_sync_state",
    "warnings",
]

SYNC_EVENT_FIELDS = [
    "timestamp",
    "source",
    "target",
    "status",
    "backup_directory",
    "message",
]


def resolve_state_root(policy: ExecutionPolicy | None = None) -> Path:
    """Use USB-backed state when the required mount is active; else repo-local data/."""
    resolved_policy = policy or load_execution_policy()
    if (
        REQUIRED_BACKUP_MOUNT.is_mount()
        and resolved_policy.backup_root_is_under_required_mount()
    ):
        return REQUIRED_BACKUP_MOUNT / STATE_DIRNAME
    return DATA_DIR


def _ensure_state_root(state_root: Path | None = None) -> Path:
    root = (state_root or resolve_state_root()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _append_csv(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fieldnames})


def _operation_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _timestamp(),
        "event_type": event_type,
        **payload,
    }


def _append_operation(event_type: str, payload: dict[str, Any], *, state_root: Path | None = None) -> Path:
    root = _ensure_state_root(state_root)
    _append_jsonl(root / OPERATIONS_JSONL, _operation_payload(event_type, payload))
    return root


def _load_manifest_payload(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_backup_execution(result, *, state_root: Path | None = None) -> None:
    if not result.executed or result.manifest is None:
        return
    root = _append_operation(
        "backup_executed",
        {
            "database": result.database,
            "backup_kind": result.backup_kind,
            "backup_id": result.manifest.backup_id,
            "backup_directory_path": result.backup_directory_path,
            "dump_file": result.dump_file,
            "schema_file": result.schema_file,
            "size_bytes": result.manifest.size_bytes,
            "verified": result.manifest.verified,
            "source_role": result.manifest.source_role,
            "warnings": result.safety_notes,
        },
        state_root=state_root,
    )
    _append_csv(
        root / DATABASE_BACKUPS_CSV,
        DATABASE_BACKUP_FIELDS,
        {
            "timestamp": _timestamp(),
            "database": result.database,
            "role": result.manifest.source_role,
            "event": "backup",
            "backup_kind": result.backup_kind,
            "backup_id": result.manifest.backup_id,
            "backup_path": result.backup_directory_path,
            "dump_file": result.dump_file,
            "schema_file": result.schema_file,
            "size_bytes": result.manifest.size_bytes,
            "verified": result.manifest.verified,
            "restore_check_status": "",
            "warnings": " | ".join(result.safety_notes),
        },
    )


def record_backup_verification(result, *, state_root: Path | None = None) -> None:
    backup_dir = Path(result.manifest_path).parent
    manifest = _load_manifest_payload(backup_dir)
    root = _append_operation(
        "backup_verified" if result.verified else "backup_verification_failed",
        {
            "database": result.database,
            "backup_kind": result.backup_kind,
            "backup_id": result.backup_id,
            "backup_directory_path": str(backup_dir),
            "verified": result.verified,
            "issues": result.issues,
        },
        state_root=state_root,
    )
    _append_csv(
        root / DATABASE_BACKUPS_CSV,
        DATABASE_BACKUP_FIELDS,
        {
            "timestamp": _timestamp(),
            "database": result.database,
            "role": manifest.get("source_role", ""),
            "event": "verify",
            "backup_kind": result.backup_kind,
            "backup_id": result.backup_id,
            "backup_path": str(backup_dir),
            "dump_file": manifest.get("dump_file", ""),
            "schema_file": manifest.get("schema_file", ""),
            "size_bytes": manifest.get("size_bytes", ""),
            "verified": result.verified,
            "restore_check_status": "",
            "warnings": " | ".join(result.issues),
        },
    )


def record_restore_check_result(
    result: RestoreExecutionResult,
    *,
    state_root: Path | None = None,
) -> None:
    if "_restorecheck_" not in result.target_database:
        return
    if result.dry_run and not result.executed and not result.refused:
        return
    backup_dir = Path(result.dump_path).resolve().parent
    manifest = _load_manifest_payload(backup_dir)
    status = "passed" if result.executed else "failed"
    root = _append_operation(
        "restore_check_passed" if result.executed else "restore_check_failed",
        {
            "database": result.source_database,
            "target_database": result.target_database,
            "backup_id": manifest.get("backup_id"),
            "backup_directory_path": str(backup_dir),
            "cleanup_dropped": result.cleanup_dropped,
            "message": result.message,
        },
        state_root=state_root,
    )
    _append_csv(
        root / DATABASE_BACKUPS_CSV,
        DATABASE_BACKUP_FIELDS,
        {
            "timestamp": _timestamp(),
            "database": result.source_database,
            "role": manifest.get("source_role", ""),
            "event": "restore_check",
            "backup_kind": manifest.get("backup_kind", ""),
            "backup_id": manifest.get("backup_id", ""),
            "backup_path": str(backup_dir),
            "dump_file": manifest.get("dump_file", ""),
            "schema_file": manifest.get("schema_file", ""),
            "size_bytes": manifest.get("size_bytes", ""),
            "verified": manifest.get("verified", ""),
            "restore_check_status": status,
            "warnings": result.message,
        },
    )


def record_repo_bundle_execution(plan, *, state_root: Path | None = None) -> None:
    root = _ensure_state_root(state_root)
    for entry in plan.entries:
        if not entry.executed:
            continue
        payload = {
            "repo_name": entry.display_name,
            "repo_key": entry.key,
            "path": str(entry.repo_path),
            "branch": entry.branch,
            "commit": entry.commit,
            "remote": entry.remote_url,
            "dirty": entry.dirty,
            "untracked_count": entry.untracked_count,
            "bundle_path": str(entry.planned_bundle_path),
            "bundle_verified": entry.bundle_verified,
            "bundle_size_bytes": entry.bundle_size_bytes,
            "warnings": [
                "Repository was dirty at bundle time." if entry.dirty else "",
                entry.error or "",
            ],
        }
        _append_jsonl(root / OPERATIONS_JSONL, _operation_payload("repo_bundle_written", payload))
        _append_csv(
            root / REPO_BUNDLES_CSV,
            REPO_BUNDLE_FIELDS,
            {
                "timestamp": _timestamp(),
                "repo_name": entry.display_name,
                "path": str(entry.repo_path),
                "branch": entry.branch,
                "commit": entry.commit,
                "remote": entry.remote_url,
                "dirty": entry.dirty,
                "untracked_count": entry.untracked_count,
                "bundle_path": str(entry.planned_bundle_path),
                "bundle_verified": entry.bundle_verified,
                "bundle_size_bytes": entry.bundle_size_bytes or "",
                "warnings": " | ".join(
                    item for item in ["Repository was dirty at bundle time." if entry.dirty else "", entry.error or ""] if item
                ),
            },
        )


def record_repo_bundle_retention(plan, *, state_root: Path | None = None) -> None:
    root = _ensure_state_root(state_root)
    for entry in plan.entries:
        pruned_bundles = [str(path) for path in entry.pruned_bundle_paths]
        pruned_manifests = [str(path) for path in entry.pruned_manifest_paths]
        pruned_runbooks = [str(path) for path in entry.pruned_runbook_paths]
        if not (pruned_bundles or pruned_manifests or pruned_runbooks):
            continue
        _append_jsonl(
            root / OPERATIONS_JSONL,
            _operation_payload(
                "repo_bundle_retention_pruned",
                {
                    "repo_name": entry.display_name,
                    "repo_key": entry.key,
                    "bundle_path": str(entry.planned_bundle_path),
                    "pruned_bundle_paths": pruned_bundles,
                    "pruned_manifest_paths": pruned_manifests,
                    "pruned_runbook_paths": pruned_runbooks,
                },
            ),
        )


def record_transfer_bundle_written(bundle, *, state_root: Path | None = None) -> None:
    root = _append_operation(
        "transfer_bundle_written",
        {
            "manifest_path": bundle.transfer_manifest_path,
            "runbook_path": bundle.transfer_runbook_path,
            "database_sources": len(bundle.database_entries),
            "verified_sources": sum(1 for entry in bundle.database_entries if entry.verified),
            "repo_count": len(bundle.repo_entries),
            "dirty_repo_count": len(bundle.dirty_repo_names),
            "sync_ready": bundle.ready_sync_pairs,
            "sync_blocked": bundle.blocked_sync_pairs,
            "actual_sync_state": "deferred",
            "warnings": bundle.warnings,
        },
        state_root=state_root,
    )
    _append_csv(
        root / TRANSFER_PACKAGES_CSV,
        TRANSFER_PACKAGE_FIELDS,
        {
            "timestamp": _timestamp(),
            "manifest_path": bundle.transfer_manifest_path,
            "runbook_path": bundle.transfer_runbook_path,
            "database_sources": len(bundle.database_entries),
            "verified_sources": sum(1 for entry in bundle.database_entries if entry.verified),
            "repo_count": len(bundle.repo_entries),
            "dirty_repo_count": len(bundle.dirty_repo_names),
            "sync_ready": bundle.ready_sync_pairs,
            "sync_blocked": bundle.blocked_sync_pairs,
            "actual_sync_state": "deferred",
            "warnings": " | ".join(bundle.warnings),
        },
    )


def record_sync_batch_execution(batch, *, state_root: Path | None = None) -> None:
    root = _ensure_state_root(state_root)
    for result in batch.results:
        if not (result.executed or result.refused):
            continue
        status = "executed" if result.executed else "refused"
        payload = {
            "source": result.source,
            "target": result.target,
            "status": status,
            "backup_directory": result.backup_dir,
            "message": result.message,
        }
        _append_jsonl(root / OPERATIONS_JSONL, _operation_payload("sync_" + status, payload))
        _append_csv(
            root / SYNC_EVENTS_CSV,
            SYNC_EVENT_FIELDS,
            {
                "timestamp": _timestamp(),
                "source": result.source,
                "target": result.target,
                "status": status,
                "backup_directory": result.backup_dir or "",
                "message": result.message,
            },
        )
