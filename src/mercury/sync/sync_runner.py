"""Prod→dev sync execution helpers (restore verified backup into dev target)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import SYNC_DEV_CONFIRMATION_PHRASE
from mercury.restore.restore_runner import execute_restore_into_database
from mercury.sync.readiness import SyncReadinessEntry


class SyncExecutionResult(BaseModel):
    source: str
    target: str
    executed: bool = False
    dry_run: bool = True
    backup_dir: str | None = None
    message: str = ""
    verification_passed: bool | None = None


class SyncBatchResult(BaseModel):
    confirmation_phrase: str = SYNC_DEV_CONFIRMATION_PHRASE
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
) -> SyncBatchResult:
    """Plan or execute prod→dev sync for ready pairs only."""
    batch = SyncBatchResult()
    if execute and not policy.live_execution_allowed():
        reason = policy.refusal_reason() or "Live sync is not permitted."
        for entry in entries:
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                backup_dir=entry.latest_backup_dir,
                message=reason,
                verification_passed=None,
            )
            )
        batch.refused_count = len(entries)
        return batch

    for entry in entries:
        if not entry.ready_for_sync_planning:
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                    message="Blocked — rescan readiness or prepare backups first.",
                    verification_passed=None,
                )
            )
            batch.refused_count += 1
            continue

        dump_path = _resolve_dump_path(entry)
        if dump_path is None:
            batch.results.append(
                SyncExecutionResult(
                    source=entry.prod,
                    target=entry.expected_dev,
                    backup_dir=entry.latest_backup_dir,
                    message="Verified backup dump file not found on disk.",
                    verification_passed=None,
                )
            )
            batch.refused_count += 1
            continue

        restore = execute_restore_into_database(
            target_database=entry.expected_dev,
            dump_path=dump_path,
            source_database=entry.prod,
            execute=execute,
            policy=policy,
            recreate_target=True,
            import_runner=import_runner,
        )
        batch.results.append(
            SyncExecutionResult(
                source=entry.prod,
                target=entry.expected_dev,
                executed=restore.executed,
                dry_run=restore.dry_run,
                backup_dir=entry.latest_backup_dir,
                message=restore.message,
                verification_passed=restore.verification_passed,
            )
        )
        if restore.executed and restore.verification_passed is not False:
            batch.executed_count += 1
        elif restore.dry_run:
            batch.dry_run_count += 1
        else:
            batch.refused_count += 1

    if execute:
        from mercury.state.ledger import record_sync_batch_execution

        record_sync_batch_execution(batch)
    return batch


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
