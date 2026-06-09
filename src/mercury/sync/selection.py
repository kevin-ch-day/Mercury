"""Selection helpers for ready production-to-development sync pairs."""

from __future__ import annotations

from mercury.sync.readiness import SyncReadinessEntry


def select_sync_entries(
    entries: list[SyncReadinessEntry],
    *,
    source: str | None = None,
    target: str | None = None,
) -> list[SyncReadinessEntry]:
    """Select ready sync entries by source and/or target, preserving order."""
    selected = entries
    if source:
        selected = [entry for entry in selected if entry.prod == source]
    if target:
        selected = [entry for entry in selected if entry.expected_dev == target]
    return selected
