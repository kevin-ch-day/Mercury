"""Summaries for the portable Mercury operation ledger."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mercury import output
from mercury.state.ledger import (
    DATABASE_BACKUPS_CSV,
    OPERATIONS_JSONL,
    REPO_BUNDLES_CSV,
    STATE_DIRNAME,
    SYNC_EVENTS_CSV,
    TRANSFER_PACKAGES_CSV,
    read_operator_database_backup_rows,
    resolve_state_root,
)


@dataclass(frozen=True)
class StateSummary:
    state_root: Path
    source: str
    operations: int
    database_backup_rows: int
    repo_bundle_rows: int
    transfer_package_rows: int
    sync_event_rows: int


def _count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    return max(0, len(lines) - 1)


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def build_state_summary(*, state_root: Path | None = None) -> StateSummary:
    root = (state_root or resolve_state_root()).expanduser().resolve()
    source = "usb" if root.name == STATE_DIRNAME else "repo-local fallback"
    return StateSummary(
        state_root=root,
        source=source,
        operations=_count_jsonl(root / OPERATIONS_JSONL),
        database_backup_rows=len(read_operator_database_backup_rows(state_root=root)),
        repo_bundle_rows=_count_rows(root / REPO_BUNDLES_CSV),
        transfer_package_rows=_count_rows(root / TRANSFER_PACKAGES_CSV),
        sync_event_rows=_count_rows(root / SYNC_EVENTS_CSV),
    )


def print_state_summary(summary: StateSummary) -> None:
    output.heading("MERCURY STATE SUMMARY")
    output.field("state_root", summary.state_root)
    output.field("source", summary.source)
    output.field("operations", summary.operations)
    output.field("database_backups", summary.database_backup_rows)
    output.field("repo_bundles", summary.repo_bundle_rows)
    output.field("transfer_packages", summary.transfer_package_rows)
    output.field("sync_events", summary.sync_event_rows)
