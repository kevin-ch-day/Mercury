"""Read-only verification of dev targets against verified prod backup baselines."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.core.runtime import should_probe_database_status
from mercury.sync.readiness import build_sync_readiness_report
from mercury.restore.readiness import (
    TargetCompletenessEntry,
    build_target_completeness_entry_against_backup,
)


class SyncVerificationEntry(BaseModel):
    source: str
    target: str
    project: str | None = None
    status: str
    ready: bool = False
    backup_id: str | None = None
    live_objects: int | None = None
    backup_objects: int | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SyncVerificationReport(BaseModel):
    mode: str
    entries: list[SyncVerificationEntry] = Field(default_factory=list)
    complete_count: int = 0
    incomplete_count: int = 0
    unknown_count: int = 0


def _entry_from_target(entry: TargetCompletenessEntry, *, source: str, target: str, project: str | None) -> SyncVerificationEntry:
    return SyncVerificationEntry(
        source=source,
        target=target,
        project=project,
        status=entry.completeness_status,
        ready=entry.completeness_status == "complete",
        backup_id=entry.backup_id,
        live_objects=entry.live_object_count,
        backup_objects=entry.backup_object_count,
        blockers=list(entry.blockers),
        warnings=list(entry.warnings),
    )


def build_sync_verification_report(*, live: bool = True) -> SyncVerificationReport:
    readiness = build_sync_readiness_report(live=live)
    probe = live and should_probe_database_status()
    entries: list[SyncVerificationEntry] = []
    for pair in readiness.entries:
        target = build_target_completeness_entry_against_backup(
            source_database=pair.prod,
            target_database=pair.expected_dev,
            live=probe,
        )
        entries.append(
            _entry_from_target(
                target,
                source=pair.prod,
                target=pair.expected_dev,
                project=pair.project,
            )
        )
    complete = sum(1 for entry in entries if entry.status == "complete")
    incomplete = sum(1 for entry in entries if entry.status == "incomplete")
    unknown = len(entries) - complete - incomplete
    return SyncVerificationReport(
        mode="live" if probe else "offline",
        entries=entries,
        complete_count=complete,
        incomplete_count=incomplete,
        unknown_count=unknown,
    )

