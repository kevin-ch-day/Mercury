"""Handoff history from the portable Mercury state ledger."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.state.ledger import (
    read_operator_database_bundle_rows,
    read_operator_operation_rows,
    read_operator_transfer_package_rows,
)
from mercury.terminal.format import format_human_datetime


class HandoffHistoryEntry(BaseModel):
    timestamp: str
    event: str
    handoff_status: str
    detail: str
    path: str = ""


class HandoffHistoryReport(BaseModel):
    entries: list[HandoffHistoryEntry] = Field(default_factory=list)
    transfer_package_count: int = 0
    database_bundle_count: int = 0
    wizard_run_count: int = 0


def _display_timestamp(raw: str) -> str:
    if not raw:
        return "—"
    return format_human_datetime(raw)


def build_handoff_history(*, limit: int = 12) -> HandoffHistoryReport:
    """Merge transfer, database bundle, and wizard events into one operator timeline."""
    entries: list[HandoffHistoryEntry] = []
    transfer_rows = read_operator_transfer_package_rows()
    bundle_rows = read_operator_database_bundle_rows()
    wizard_rows = [
        row
        for row in read_operator_operation_rows()
        if row.get("event_type") == "handoff_wizard_run"
    ]

    for row in transfer_rows:
        entries.append(
            HandoffHistoryEntry(
                timestamp=str(row.get("timestamp") or ""),
                event="transfer package",
                handoff_status=str(row.get("handoff_status") or "unknown"),
                detail=(
                    f"{row.get('verified_sources', '?')} verified sources; "
                    f"database={row.get('database_package', '?')}; "
                    f"repo={row.get('repository_package', '?')}"
                ),
                path=str(row.get("manifest_path") or ""),
            )
        )
    for row in bundle_rows:
        entries.append(
            HandoffHistoryEntry(
                timestamp=str(row.get("timestamp") or ""),
                event="database bundle",
                handoff_status=str(row.get("package_status") or "unknown"),
                detail=(
                    f"{row.get('verified_count', '?')} of {row.get('source_count', '?')} verified; "
                    f"{row.get('stale_count', 0)} stale"
                ),
                path=str(row.get("index_manifest_path") or ""),
            )
        )
    for row in wizard_rows:
        phases = row.get("phases") or []
        failed = sum(1 for phase in phases if phase.get("status") == "failed")
        entries.append(
            HandoffHistoryEntry(
                timestamp=str(row.get("timestamp") or ""),
                event="guided wizard",
                handoff_status=str(row.get("final_handoff_status") or "unknown"),
                detail=f"{len(phases)} phase(s); {failed} failed",
                path="",
            )
        )

    entries.sort(key=lambda entry: entry.timestamp, reverse=True)
    capped = entries[: max(1, limit)]
    return HandoffHistoryReport(
        entries=capped,
        transfer_package_count=len(transfer_rows),
        database_bundle_count=len(bundle_rows),
        wizard_run_count=len(wizard_rows),
    )
