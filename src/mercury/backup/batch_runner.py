"""Batch backup planning and execution for all backup sources."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.backup.backup_runner import BackupExecutionError, BackupExecutionResult, execute_backup
from mercury.backup.manifest import BackupKind
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.database.core import classify_database
from mercury.database.backup_planning import build_backup_plan_from_inventory


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


def resolve_batch_sources(*, live: bool = False) -> list[str]:
    """Backup source names from live server or demo/config inventory."""
    from mercury.database.discovery import discover_for_planning

    inventory = discover_for_planning(live=live)
    plan = build_backup_plan_from_inventory(inventory)
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


def run_backup_batch(
    kind: BackupKind,
    *,
    execute: bool = False,
    live: bool = True,
    policy: ExecutionPolicy | None = None,
    sources: list[str] | None = None,
    dump_runner=None,
) -> BackupBatchResult:
    """Plan or execute backups for all approved backup sources."""
    resolved_policy = policy or load_execution_policy()
    batch_sources = sources or resolve_batch_sources(live=live)
    batch = BackupBatchResult(
        backup_kind=kind,
        execute=execute,
        sources=batch_sources,
    )

    for database in batch_sources:
        try:
            result = execute_backup(
                database,
                kind,
                execute=execute,
                policy=resolved_policy,
                dump_runner=dump_runner,
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
