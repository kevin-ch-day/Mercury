"""Prod→dev sync execution helpers (restore verified backup into dev target)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.prod_dev_pairs import (
    APPROVED_SYNC_PAIR_BY_SOURCE,
    schema_rewrites_for_pair,
)
from mercury.restore.restore_runner import execute_restore_into_database
from mercury.sync.readiness import SyncReadinessEntry
from mercury.sync.selection import order_sync_entries

SYNC_CONFIRMATION_PHRASE = "SYNC DEV"


class SyncExecutionResult(BaseModel):
    source: str
    target: str
    executed: bool = False
    dry_run: bool = True
    refused: bool = False
    backup_dir: str | None = None
    message: str = ""
    verification_passed: bool | None = None
    isolation_passed: bool | None = None
    isolation_receipt: str | None = None


class SyncBatchResult(BaseModel):
    results: list[SyncExecutionResult] = Field(default_factory=list)
    executed_count: int = 0
    dry_run_count: int = 0
    refused_count: int = 0


def run_sync_batch(
    entries: list[SyncReadinessEntry],
    *,
    execute: bool,
    policy: ExecutionPolicy,
    import_runner=None,
    confirmation_phrase: str | None = None,
    on_import_progress=None,
    isolation_fn=None,
) -> SyncBatchResult:
    """Plan or execute prod→dev sync for ready pairs only."""
    batch = SyncBatchResult()
    ordered = order_sync_entries(entries)
    if execute and confirmation_phrase != SYNC_CONFIRMATION_PHRASE:
        for entry in ordered:
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                    backup_dir=entry.latest_backup_dir,
                    refused=True,
                    verification_passed=None,
                    message="Sync refused before target modification: type SYNC DEV to confirm development replacement.",
                )
            )
        batch.refused_count = len(ordered)
        return batch
    if execute and not policy.live_execution_allowed():
        reason = policy.refusal_reason() or "Live sync is not permitted."
        for entry in ordered:
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                    backup_dir=entry.latest_backup_dir,
                    message=reason,
                    refused=True,
                    verification_passed=None,
                )
            )
        batch.refused_count = len(ordered)
        return batch

    failed_sources: set[str] = set()
    for entry in ordered:
        unmet = [
            dep
            for dep in _depends_on(entry)
            if dep in failed_sources
        ]
        if unmet:
            labels = ", ".join(unmet)
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                    backup_dir=entry.latest_backup_dir,
                    refused=True,
                    verification_passed=None,
                    message=(
                        "Skipped because required upstream refresh failed: "
                        f"{labels}. Dependent development target was not modified."
                    ),
                )
            )
            batch.refused_count += 1
            failed_sources.add(entry.prod)
            continue

        if not entry.ready_for_sync_planning:
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                    message="Blocked — rescan readiness or prepare backups first.",
                    refused=True,
                    verification_passed=None,
                )
            )
            batch.refused_count += 1
            failed_sources.add(entry.prod)
            continue

        dump_path = _resolve_dump_path(entry)
        if dump_path is None:
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                    backup_dir=entry.latest_backup_dir,
                    message="Verified backup dump file not found on disk.",
                    refused=True,
                    verification_passed=None,
                )
            )
            batch.refused_count += 1
            failed_sources.add(entry.prod)
            continue

        rewrites = schema_rewrites_for_pair(entry.prod, entry.expected_dev)
        restore_cfg = None
        preflight = None
        if execute:
            from mercury.sync.restore_preflight import evaluate_restore_privileges
            from mercury.database.mariadb.config import MariaDbConfigError, load_mariadb_restore_config

            try:
                restore_cfg = load_mariadb_restore_config()
            except MariaDbConfigError as exc:
                batch.results.append(SyncExecutionResult(
                    source=entry.prod, target=entry.expected_dev, backup_dir=entry.latest_backup_dir,
                    refused=True, verification_passed=None,
                    message=(
                        "Restore credentials refused before target modification: "
                        f"{exc}"
                    ),
                ))
                batch.refused_count += 1
                failed_sources.add(entry.prod)
                continue

            preflight = evaluate_restore_privileges(
                source=entry.prod,
                target=entry.expected_dev,
                backup_dir=Path(entry.latest_backup_dir),
                config=restore_cfg,
                schema_rewrites=rewrites,
            )
            if not preflight.passed:
                detail = ", ".join(preflight.missing_capabilities or preflight.unknown_requirements or preflight.inspection_issues)
                batch.results.append(SyncExecutionResult(
                    source=entry.prod, target=entry.expected_dev, backup_dir=entry.latest_backup_dir,
                    refused=True, verification_passed=None,
                    message=f"Restore privilege preflight refused before target modification: {detail}",
                ))
                batch.refused_count += 1
                failed_sources.add(entry.prod)
                continue

        restore = execute_restore_into_database(
            target_database=entry.expected_dev,
            dump_path=dump_path,
            source_database=entry.prod,
            execute=execute,
            policy=policy,
            config=restore_cfg if execute else None,
            recreate_target=True,
            import_runner=import_runner,
            schema_rewrites=rewrites,
            on_import_progress=(
                (
                    lambda uncompressed, compressed, elapsed, _entry=entry:
                    on_import_progress(_entry, uncompressed, compressed, elapsed)
                )
                if on_import_progress is not None
                else None
            ),
            restore_preflight=preflight if execute else None,
            require_restore_preflight=execute,
        )
        isolation_passed: bool | None = None
        isolation_receipt: str | None = None
        message = restore.message
        if restore.executed and restore.verification_passed is not False and execute and restore_cfg is not None:
            checker = _check_isolation if isolation_fn is None else isolation_fn
            isolation = checker(entry.expected_dev, restore_cfg)
            isolation_passed = isolation.isolation == "PASS"
            isolation_receipt = isolation.format_receipt()
            if not isolation_passed:
                message = (
                    f"{restore.message} Isolation check failed: "
                    + "; ".join(isolation.issues or [isolation.format_receipt()])
                )
        batch.results.append(
            SyncExecutionResult(
                source=entry.prod,
                target=entry.expected_dev,
                executed=restore.executed,
                dry_run=restore.dry_run,
                refused=restore.refused or isolation_passed is False,
                backup_dir=entry.latest_backup_dir,
                message=message,
                verification_passed=restore.verification_passed,
                isolation_passed=isolation_passed,
                isolation_receipt=isolation_receipt,
            )
        )
        if isolation_passed is False:
            batch.refused_count += 1
            failed_sources.add(entry.prod)
        elif restore.executed and restore.verification_passed is not False:
            batch.executed_count += 1
        elif restore.dry_run:
            batch.dry_run_count += 1
        else:
            batch.refused_count += 1
            failed_sources.add(entry.prod)

    if execute:
        from mercury.state.ledger import record_sync_batch_execution

        record_sync_batch_execution(batch)
    return batch


def _depends_on(entry: SyncReadinessEntry) -> list[str]:
    if entry.depends_on_sources:
        return list(entry.depends_on_sources)
    spec = APPROVED_SYNC_PAIR_BY_SOURCE.get(entry.prod)
    return list(spec.depends_on_sources) if spec else []


def _check_isolation(target: str, config):
    from mercury.sync.isolation import inspect_restored_schema_isolation

    return inspect_restored_schema_isolation(target, config=config)


def _resolve_dump_path(entry: SyncReadinessEntry) -> Path | None:
    if not entry.latest_backup_dir:
        return None
    backup_dir = Path(entry.latest_backup_dir)
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    import json

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    dump_name = data.get("dump_file")
    if not dump_name:
        return None
    dump_path = backup_dir / dump_name
    return dump_path if dump_path.is_file() else None
