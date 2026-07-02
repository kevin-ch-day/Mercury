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
from mercury.terminal.format import format_human_datetime


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
    latest_handoff_status: str | None = None
    latest_transfer_manifest: str | None = None
    latest_transfer_at: str | None = None
    latest_database_bundle_status: str | None = None
    wizard_run_count: int = 0


def _latest_transfer_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: row.get("timestamp", ""))


def _latest_bundle_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: row.get("timestamp", ""))


def build_state_summary(*, state_root: Path | None = None) -> StateSummary:
    root = (state_root or resolve_state_root()).expanduser().resolve()
    source = "usb" if root.name == STATE_DIRNAME else "repo-local fallback"
    transfer_rows = read_operator_transfer_package_rows(state_root=root)
    bundle_rows = read_operator_database_bundle_rows(state_root=root)
    operation_rows = read_operator_operation_rows(state_root=root)
    latest_transfer = _latest_transfer_row(transfer_rows)
    latest_bundle = _latest_bundle_row(bundle_rows)
    wizard_runs = sum(1 for row in operation_rows if row.get("event_type") == "handoff_wizard_run")
    latest_transfer_at = None
    if latest_transfer and latest_transfer.get("timestamp"):
        latest_transfer_at = format_human_datetime(str(latest_transfer["timestamp"]))
    return StateSummary(
        state_root=root,
        source=source,
        operations=len(operation_rows),
        database_backup_rows=len(read_operator_database_backup_rows(state_root=root)),
        database_bundle_rows=len(bundle_rows),
        repo_bundle_rows=len(read_operator_repo_bundle_rows(state_root=root)),
        transfer_package_rows=len(transfer_rows),
        sync_event_rows=len(read_operator_sync_event_rows(state_root=root)),
        latest_handoff_status=(
            str(latest_transfer.get("handoff_status"))
            if latest_transfer and latest_transfer.get("handoff_status")
            else None
        ),
        latest_transfer_manifest=(
            str(latest_transfer.get("manifest_path"))
            if latest_transfer and latest_transfer.get("manifest_path")
            else None
        ),
        latest_transfer_at=latest_transfer_at,
        latest_database_bundle_status=(
            str(latest_bundle.get("package_status"))
            if latest_bundle and latest_bundle.get("package_status")
            else None
        ),
        wizard_run_count=wizard_runs,
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
    if summary.latest_handoff_status:
        output.field("latest_handoff_status", summary.latest_handoff_status)
    if summary.latest_transfer_at:
        output.field("latest_transfer_on_usb", summary.latest_transfer_at)
    if summary.latest_database_bundle_status:
        output.field("latest_database_bundle_status", summary.latest_database_bundle_status)
    if summary.wizard_run_count:
        output.field("guided_wizard_runs", summary.wizard_run_count)
