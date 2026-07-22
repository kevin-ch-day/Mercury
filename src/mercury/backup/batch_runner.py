"""Batch backup planning and execution for all backup sources."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.backup_runner import BackupExecutionError, BackupExecutionResult, execute_backup
from mercury.backup.manifest import BackupKind
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.database.core import classify_database
from mercury.backup.live_inventory import fetch_live_server_database_names
from mercury.database.backup_planning import build_backup_plan_from_inventory

# Compressed full dumps smaller than this for production sources with tables
# warrant an explicit operator warning (not an automatic failure).
UNEXPECTEDLY_SMALL_PRODUCTION_BYTES = 16 * 1024


class BackupBatchResult(BaseModel):
    backup_kind: BackupKind
    execute: bool
    sources: list[str] = Field(default_factory=list)
    results: list[BackupExecutionResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    executed_count: int = 0
    refused_count: int = 0
    dry_run_count: int = 0


class BackupSourceSelectionError(ValueError):
    """Selected backup source list is not valid for the current scope."""


class BatchVerificationSummary(BaseModel):
    verified: int = 0
    failed: int = 0
    issues: list[str] = Field(default_factory=list)
    backup_ids: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)


# Backward-compatible alias used by development-only helpers and CLI.
DevelopmentBackupVerification = BatchVerificationSummary


class FullBackupOutcome(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    REFUSED = "REFUSED"


class BackupLaneSummary(BaseModel):
    requested: bool = False
    selected: int = 0
    written: int = 0
    verified: int = 0
    failed: int = 0
    refused: int = 0
    total_size_bytes: int = 0
    backup_ids: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    verification_evidence_paths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    small_backup_warnings: list[str] = Field(default_factory=list)


class FullBackupRunResult(BaseModel):
    run_id: str
    outcome: FullBackupOutcome
    started_at_utc: str
    finished_at_utc: str
    production: BackupLaneSummary
    development: BackupLaneSummary
    receipt_path: str | None = None
    package_classification: str = "routine_only"
    phase3b_separation_note: str = (
        "Routine verified backups do not replace the sealed Phase 3B rehearsal package "
        "until restore-check and handoff packaging explicitly select them."
    )
    next_actions: list[str] = Field(default_factory=list)
    overall_written: int = 0
    overall_verified: int = 0
    overall_failed: int = 0


def resolve_batch_sources(*, live: bool = False) -> list[str]:
    """Backup source names from live server or demo/config inventory."""
    from mercury.database.discovery import discover_for_planning

    inventory = discover_for_planning(live=live)
    plan = build_backup_plan_from_inventory(inventory, live=live)
    return list(plan.backup_sources)


def select_batch_sources(
    *,
    selected: list[str] | None = None,
    live: bool = False,
) -> list[str]:
    """Resolve current backup sources and validate any requested filter."""
    available = resolve_batch_sources(live=live)
    if not selected:
        return available

    available_set = set(available)
    resolved: list[str] = []
    seen: set[str] = set()
    for database in selected:
        if database in seen:
            continue
        seen.add(database)

        classification = classify_database(database)
        if not classification.backup_source:
            raise BackupSourceSelectionError(
                f"Refusing backup selection for '{database}': not an approved backup source."
            )
        if database not in available_set:
            raise BackupSourceSelectionError(
                f"Backup source '{database}' is not in the current active backup scope."
            )
        resolved.append(database)

    return resolved


def resolve_development_backup_sources(*, live: bool = False) -> list[str]:
    """Configured dev targets that exist in the current inventory.

    This is intentionally separate from routine source protection.  It is for
    a deliberate pre-migration/recovery capture, never a default backup lane.
    """
    from mercury.database.discovery import discover_for_planning
    from mercury.database.core.scope import is_active_dev_recovery_database

    inventory = discover_for_planning(live=live)
    return sorted(name for name in inventory.names if is_active_dev_recovery_database(name))


def run_backup_batch(
    kind: BackupKind,
    *,
    execute: bool = True,
    live: bool = True,
    policy: ExecutionPolicy | None = None,
    sources: list[str] | None = None,
    dump_runner=None,
    allow_development_backup: bool = False,
) -> BackupBatchResult:
    """Plan or execute backups for all approved backup sources."""
    resolved_policy = policy or load_execution_policy()
    batch_sources = sources or resolve_batch_sources(live=live)
    batch = BackupBatchResult(
        backup_kind=kind,
        execute=execute,
        sources=batch_sources,
    )
    server_names = fetch_live_server_database_names() if live else None

    for database in batch_sources:
        try:
            result = execute_backup(
                database,
                kind,
                execute=execute,
                policy=resolved_policy,
                dump_runner=dump_runner,
                live=live,
                server_names=server_names,
                allow_development_backup=allow_development_backup,
            )
        except BackupExecutionError as exc:
            batch.errors.append(f"{database}: {exc}")
            continue

        batch.results.append(result)
        if result.executed:
            batch.executed_count += 1
        elif result.refused:
            batch.refused_count += 1
        else:
            batch.dry_run_count += 1

    from mercury.logging.events import log_batch_backup

    log_batch_backup(
        backup_kind=kind,
        execute=execute,
        source_count=len(batch_sources),
        executed=batch.executed_count,
        dry_run=batch.dry_run_count,
        refused=batch.refused_count,
        errors=len(batch.errors),
    )
    return batch


def verify_written_backup_batch(
    batch: BackupBatchResult,
    *,
    allow_development_backup: bool = False,
) -> BatchVerificationSummary:
    """Verify newly written backups from this batch by exact directory / backup ID."""
    from mercury.backup.verification import verify_backup_directory

    summary = BatchVerificationSummary()
    for result in batch.results:
        if not result.executed or not result.backup_directory_path:
            continue
        expected_id = result.manifest.backup_id if result.manifest else None
        verification = verify_backup_directory(
            Path(result.backup_directory_path),
            database=result.database,
            update_manifest=True,
            allow_development_backup=allow_development_backup,
        )
        evidence = getattr(verification, "manifest_path", None) or str(
            Path(result.backup_directory_path) / "manifest.json"
        )
        summary.evidence_paths.append(evidence)
        verified_id = getattr(verification, "backup_id", None) or expected_id
        if expected_id and verified_id and verified_id != expected_id:
            summary.failed += 1
            summary.issues.append(
                f"{result.database}: verified unexpected backup_id "
                f"{verified_id!r} (expected {expected_id!r})"
            )
            continue
        if verification.verified:
            summary.verified += 1
            if verified_id:
                summary.backup_ids.append(verified_id)
        else:
            summary.failed += 1
            summary.issues.append(
                f"{result.database} ({verified_id or 'unknown'}): "
                f"{(getattr(verification, 'issues', None) or ['verification failed'])[0]}"
            )
    return summary


def verify_development_backup_batch(batch: BackupBatchResult) -> BatchVerificationSummary:
    """Verify newly written optional development backups and stamp their manifests."""
    return verify_written_backup_batch(batch, allow_development_backup=True)


def small_production_backup_warning(result: BackupExecutionResult) -> str | None:
    """Warn when a newly written production full dump looks unexpectedly small."""
    if not result.executed or not result.manifest:
        return None
    size = int(result.manifest.size_bytes or 0)
    tables = 0
    if result.content_contract is not None:
        tables = len(result.content_contract.live.tables)
    if size >= UNEXPECTEDLY_SMALL_PRODUCTION_BYTES:
        return None
    if tables <= 0 and size > 0:
        return None
    return (
        f"{result.database} ({result.manifest.backup_id}): newly written full dump is only "
        f"{size} bytes with {tables} live table(s) — confirm this production catalog is "
        "expected to be this small before treating it as a handoff package member."
    )


def _lane_from_batch(
    batch: BackupBatchResult | None,
    verification: BatchVerificationSummary | None,
    *,
    requested: bool,
) -> BackupLaneSummary:
    if batch is None:
        return BackupLaneSummary(requested=requested)
    size = 0
    ids: list[str] = []
    paths: list[str] = []
    warnings: list[str] = []
    for result in batch.results:
        if result.executed and result.manifest:
            size += int(result.manifest.size_bytes or 0)
            ids.append(result.manifest.backup_id)
            if result.backup_directory_path:
                paths.append(result.backup_directory_path)
            warning = small_production_backup_warning(result)
            if warning:
                warnings.append(warning)
    verified = verification.verified if verification else 0
    failed = verification.failed if verification else 0
    issues = list(batch.errors)
    if verification:
        issues.extend(verification.issues)
    return BackupLaneSummary(
        requested=requested,
        selected=len(batch.sources),
        written=batch.executed_count,
        verified=verified,
        failed=failed + len(batch.errors),
        refused=batch.refused_count,
        total_size_bytes=size,
        backup_ids=ids,
        artifact_paths=paths,
        verification_evidence_paths=list(verification.evidence_paths) if verification else [],
        issues=issues,
        small_backup_warnings=warnings,
    )


def new_full_backup_run_id(*, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_full_backup"


def write_full_backup_run_receipt(
    result: FullBackupRunResult,
    *,
    control_root: Path | None = None,
) -> Path:
    """Persist a run-level receipt linking production/dev backup IDs (additive)."""
    if control_root is None:
        from mercury.core.usb_mount import resolve_operator_mount
        from mercury.core.storage_roles import CONTROL_DIRNAME

        control_root = resolve_operator_mount() / CONTROL_DIRNAME
    directory = control_root / "full_backup_runs"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = directory / f"{result.run_id}.json"
    payload = result.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def build_full_backup_run_result(
    *,
    run_id: str,
    started_at_utc: str,
    production_batch: BackupBatchResult,
    production_verification: BatchVerificationSummary | None,
    development_batch: BackupBatchResult | None = None,
    development_verification: BatchVerificationSummary | None = None,
    development_requested: bool = False,
) -> FullBackupRunResult:
    """Classify a full-backup operation without renaming individual backup directories."""
    from mercury.backup.menu_options import (
        ACTION_BUNDLE,
        ACTION_RESTORE_CHECK,
        backup_menu_next_actions,
    )

    production = _lane_from_batch(
        production_batch, production_verification, requested=True
    )
    development = _lane_from_batch(
        development_batch, development_verification, requested=development_requested
    )

    production_ok = (
        production.written == production.selected
        and production.failed == 0
        and production.refused == 0
        and production.verified == production.written
        and production.written > 0
        and not production_batch.errors
    )
    production_refused = (
        production.written == 0
        and (production.refused > 0 or bool(production_batch.errors))
        and production.selected > 0
    )
    development_ok = (
        not development_requested
        or (
            development.written == development.selected
            and development.failed == 0
            and development.verified == development.written
            and not (development_batch.errors if development_batch else [])
        )
    )

    if production_refused and production.written == 0:
        outcome = FullBackupOutcome.REFUSED
    elif not production_ok:
        outcome = FullBackupOutcome.FAIL
    elif development_requested and not development_ok:
        outcome = FullBackupOutcome.PARTIAL
    else:
        outcome = FullBackupOutcome.PASS

    overall_written = production.written + development.written
    overall_verified = production.verified + development.verified
    overall_failed = production.failed + development.failed

    next_actions: list[str] = []
    package_classification = "routine_only"
    if outcome == FullBackupOutcome.PASS:
        next_actions = backup_menu_next_actions(ACTION_RESTORE_CHECK, ACTION_BUNDLE)
        package_classification = "verified_routine"
    elif outcome == FullBackupOutcome.PARTIAL:
        next_actions = [
            "Resolve development backup/verification failures before treating the optional "
            "dev recovery set as complete.",
            backup_menu_next_actions(ACTION_RESTORE_CHECK)[0],
        ]
        package_classification = "verified_routine_partial_dev"

    phase3b_note = (
        "Routine verified backups remain separate from sealed Phase 3B rehearsal package "
        "20260722T055400Z_phase3b. They do not supersede Phase 3B until restore-check and "
        "handoff packaging explicitly promote them."
    )

    return FullBackupRunResult(
        run_id=run_id,
        outcome=outcome,
        started_at_utc=started_at_utc,
        finished_at_utc=datetime.now(timezone.utc).isoformat(),
        production=production,
        development=development,
        package_classification=package_classification,
        phase3b_separation_note=phase3b_note,
        next_actions=next_actions,
        overall_written=overall_written,
        overall_verified=overall_verified,
        overall_failed=overall_failed,
    )
