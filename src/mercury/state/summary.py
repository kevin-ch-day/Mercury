"""Summaries for the portable Mercury operation ledger."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mercury import output
from mercury.state.ledger import (
    STATE_DIRNAME,
    read_operator_database_backup_rows,
    read_operator_database_bundle_rows,
    read_operator_operation_rows,
    read_operator_repo_bundle_rows,
    read_operator_sync_event_rows,
    read_operator_transfer_package_rows,
    resolve_state_root,
)


@dataclass(frozen=True)
class StateSummary:
    state_root: Path
    source: str
    operations: int
    database_backup_rows: int
    database_bundle_rows: int
    repo_bundle_rows: int
    transfer_package_rows: int
    sync_event_rows: int


def build_state_summary(*, state_root: Path | None = None) -> StateSummary:
    root = (state_root or resolve_state_root()).expanduser().resolve()
    source = "usb" if root.name == STATE_DIRNAME else "repo-local fallback"
    return StateSummary(
        state_root=root,
        source=source,
        operations=len(read_operator_operation_rows(state_root=root)),
        database_backup_rows=len(read_operator_database_backup_rows(state_root=root)),
        database_bundle_rows=len(read_operator_database_bundle_rows(state_root=root)),
        repo_bundle_rows=len(read_operator_repo_bundle_rows(state_root=root)),
        transfer_package_rows=len(read_operator_transfer_package_rows(state_root=root)),
        sync_event_rows=len(read_operator_sync_event_rows(state_root=root)),
    )


def print_state_summary(summary: StateSummary) -> None:
    output.heading("MERCURY STATE SUMMARY")
    output.field("state_root", summary.state_root)
    output.field("source", summary.source)
    output.field("operations", summary.operations)
    output.field("database_backups", summary.database_backup_rows)
    output.field("database_bundles", summary.database_bundle_rows)
    output.field("repo_bundles", summary.repo_bundle_rows)
    output.field("transfer_packages", summary.transfer_package_rows)
    output.field("sync_events", summary.sync_event_rows)
