"""Append-only portable operation ledger for Mercury."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.core.paths import DATA_DIR
from mercury.core.usb_mount import usb_mount_is_active
from mercury.core.handoff_status import database_bundle_package_status

STATE_DIRNAME = "mercury_state"
OPERATIONS_JSONL = "operations.jsonl"
DATABASE_BACKUPS_CSV = "database_backups.csv"
REPO_BUNDLES_CSV = "repo_bundles.csv"
DATABASE_BUNDLES_CSV = "database_bundles.csv"
TRANSFER_PACKAGES_CSV = "transfer_packages.csv"
SYNC_EVENTS_CSV = "sync_events.csv"

ENV_STATE_ROOT = "MERCURY_STATE_ROOT"
TEST_LEDGER_PATH_MARKERS = ("/tmp/pytest", "/pytest-of-", "/pyfakefs")

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

DATABASE_BUNDLE_FIELDS = [
    "timestamp",
    "index_manifest_path",
    "index_runbook_path",
    "source_count",
    "verified_count",
    "missing_count",
    "failed_count",
    "stale_count",
    "unknown_freshness_count",
    "package_status",
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
    "handoff_status",
    "database_package",
    "repository_package",
    "stale_source_count",
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
    override = os.environ.get(ENV_STATE_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return (DATA_DIR / "pytest_state").resolve()

    resolved_policy = policy or load_execution_policy()
    if (
        usb_mount_is_active(resolved_policy.usb_mount)
        and resolved_policy.backup_root_is_under_required_mount()
    ):
        return resolved_policy.usb_mount / STATE_DIRNAME
    return DATA_DIR


def is_operator_ledger_path(path: str | None) -> bool:
    """True when a ledger backup_path belongs to operator/USB history, not pytest temp dirs."""
    if not path:
        return True
    normalized = str(path).replace("\\", "/")
    return not any(marker in normalized for marker in TEST_LEDGER_PATH_MARKERS)


def read_database_backup_rows(*, state_root: Path | None = None) -> list[dict[str, str]]:
    """Load database backup ledger rows from CSV."""
    root = (state_root or resolve_state_root()).expanduser().resolve()
    path = root / DATABASE_BACKUPS_CSV
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_operation_rows(*, state_root: Path | None = None) -> list[dict[str, Any]]:
    """Load operation ledger rows from JSONL."""
    root = (state_root or resolve_state_root()).expanduser().resolve()
    path = root / OPERATIONS_JSONL
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def read_operator_database_backup_rows(*, state_root: Path | None = None) -> list[dict[str, str]]:
    """Operator-facing ledger rows with pytest/temp paths excluded."""
    return [
        row
        for row in read_database_backup_rows(state_root=state_root)
        if is_operator_ledger_path(row.get("backup_path"))
    ]


def read_operator_operation_rows(*, state_root: Path | None = None) -> list[dict[str, Any]]:
    """Operator-facing operation rows with pytest/temp paths excluded."""
    return [
        row
        for row in read_operation_rows(state_root=state_root)
        if _operation_payload_is_operator_visible(row)
    ]


def read_operator_repo_bundle_rows(*, state_root: Path | None = None) -> list[dict[str, str]]:
    return [
        row
        for row in _read_csv_rows(REPO_BUNDLES_CSV, state_root=state_root)
        if is_operator_ledger_path(row.get("bundle_path"))
    ]


def read_operator_database_bundle_rows(*, state_root: Path | None = None) -> list[dict[str, str]]:
    return [
        row
        for row in _read_csv_rows(DATABASE_BUNDLES_CSV, state_root=state_root)
        if is_operator_ledger_path(row.get("index_manifest_path"))
        and is_operator_ledger_path(row.get("index_runbook_path"))
    ]


def read_operator_transfer_package_rows(*, state_root: Path | None = None) -> list[dict[str, str]]:
    return [
        row
        for row in _read_csv_rows(TRANSFER_PACKAGES_CSV, state_root=state_root)
        if is_operator_ledger_path(row.get("manifest_path"))
        and is_operator_ledger_path(row.get("runbook_path"))
    ]


def read_operator_sync_event_rows(*, state_root: Path | None = None) -> list[dict[str, str]]:
    return [
        row
        for row in _read_csv_rows(SYNC_EVENTS_CSV, state_root=state_root)
        if is_operator_ledger_path(row.get("backup_directory"))
    ]


def _read_csv_rows(filename: str, *, state_root: Path | None = None) -> list[dict[str, str]]:
    root = (state_root or resolve_state_root()).expanduser().resolve()
    path = root / filename
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _operation_payload_is_operator_visible(row: dict[str, Any]) -> bool:
    path_keys = (
        "backup_directory_path",
        "backup_directory",
        "backup_path",
        "bundle_path",
        "manifest_path",
        "runbook_path",
        "path",
    )
    for key in path_keys:
        if not is_operator_ledger_path(_coerce_string(row.get(key))):
            return False
    list_path_keys = (
        "pruned_bundle_paths",
        "pruned_manifest_paths",
        "pruned_runbook_paths",
    )
    for key in list_path_keys:
        value = row.get(key)
        if isinstance(value, list) and any(not is_operator_ledger_path(_coerce_string(item)) for item in value):
            return False
    return True


def _coerce_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _ensure_state_root(state_root: Path | None = None) -> Path | None:
    root = (state_root or resolve_state_root()).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return None
    return root


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, payload: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except PermissionError:
        return False
    return True


def _append_csv(path: Path, fieldnames: list[str], row: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    except PermissionError:
        return False
    return True


def _operation_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _timestamp(),
        "event_type": event_type,
        **payload,
    }


def _append_operation(event_type: str, payload: dict[str, Any], *, state_root: Path | None = None) -> Path | None:
    root = _ensure_state_root(state_root)
    if root is None:
        return None
    if not _append_jsonl(root / OPERATIONS_JSONL, _operation_payload(event_type, payload)):
        return None
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
    if root is None:
        return
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
    if root is None:
        return
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
    status = (
        "verification_failed"
        if result.verification_passed is False
        else "passed" if result.executed else "failed"
    )
    root = _append_operation(
        (
            "restore_check_verification_failed"
            if result.verification_passed is False
            else "restore_check_passed" if result.executed else "restore_check_failed"
        ),
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
    if root is None:
        return
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
    if root is None:
        return
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
    if root is None:
        return
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


def record_database_bundle_written(plan, *, state_root: Path | None = None) -> None:
    package_status = database_bundle_package_status(
        source_count=plan.source_count,
        verified_count=plan.verified_count,
        missing_count=plan.missing_count,
        failed_count=plan.failed_count,
        stale_count=plan.stale_count,
        unknown_freshness_count=plan.unknown_freshness_count,
    )
    root = _append_operation(
        "database_bundle_written",
        {
            "index_manifest_path": str(plan.planned_index_manifest_path),
            "index_runbook_path": str(plan.planned_index_runbook_path),
            "source_count": plan.source_count,
            "verified_count": plan.verified_count,
            "missing_count": plan.missing_count,
            "failed_count": plan.failed_count,
            "stale_count": plan.stale_count,
            "unknown_freshness_count": plan.unknown_freshness_count,
            "package_status": package_status,
            "warnings": plan.warnings,
            "databases": [
                {
                    "database": entry.database,
                    "manifest_path": str(entry.planned_manifest_path),
                    "runbook_path": str(entry.planned_runbook_path),
                    "protection_status": entry.protection_status,
                    "freshness": entry.freshness,
                    "backup_id": entry.backup_id,
                }
                for entry in plan.entries
            ],
        },
        state_root=state_root,
    )
    if root is None:
        return
    _append_csv(
        root / DATABASE_BUNDLES_CSV,
        DATABASE_BUNDLE_FIELDS,
        {
            "timestamp": _timestamp(),
            "index_manifest_path": str(plan.planned_index_manifest_path),
            "index_runbook_path": str(plan.planned_index_runbook_path),
            "source_count": plan.source_count,
            "verified_count": plan.verified_count,
            "missing_count": plan.missing_count,
            "failed_count": plan.failed_count,
            "stale_count": plan.stale_count,
            "unknown_freshness_count": plan.unknown_freshness_count,
            "package_status": package_status,
            "warnings": " | ".join(plan.warnings),
        },
    )


def record_transfer_bundle_written(bundle, *, state_root: Path | None = None) -> None:
    from mercury.transfer.bundle import (
        database_package_status_for_bundle,
        handoff_status_for_bundle,
        repository_package_status_for_bundle,
    )

    handoff_status = handoff_status_for_bundle(bundle)
    database_package = database_package_status_for_bundle(bundle)
    repository_package = repository_package_status_for_bundle(bundle)
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
            "handoff_status": handoff_status,
            "database_package": database_package,
            "repository_package": repository_package,
            "stale_source_count": bundle.stale_source_count,
            "warnings": bundle.warnings,
        },
        state_root=state_root,
    )
    if root is None:
        return
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
            "handoff_status": handoff_status,
            "database_package": database_package,
            "repository_package": repository_package,
            "stale_source_count": bundle.stale_source_count,
            "warnings": " | ".join(bundle.warnings),
        },
    )


def record_handoff_wizard_run(result, *, state_root: Path | None = None) -> None:
    """Append a guided handoff wizard summary to the portable ledger."""
    _append_operation(
        "handoff_wizard_run",
        {
            "final_handoff_status": result.final_handoff_status,
            "cancelled": result.cancelled,
            "phases": [
                {
                    "phase": phase.phase,
                    "status": phase.status,
                    "summary": phase.summary,
                }
                for phase in result.phases
            ],
        },
        state_root=state_root,
    )


def record_sync_batch_execution(batch, *, state_root: Path | None = None) -> None:
    root = _ensure_state_root(state_root)
    if root is None:
        return
    for result in batch.results:
        if not (result.executed or result.refused):
            continue
        status = (
            "verification_failed"
            if getattr(result, "verification_passed", None) is False
            else "executed" if result.executed else "refused"
        )
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
