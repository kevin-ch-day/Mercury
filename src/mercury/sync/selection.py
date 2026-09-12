"""Selection helpers for ready production-to-development sync pairs."""

from __future__ import annotations

from mercury.database.prod_dev_pairs import order_sync_sources
from mercury.sync.readiness import SyncReadinessEntry


def order_sync_entries(entries: list[SyncReadinessEntry]) -> list[SyncReadinessEntry]:
    """Return approved pairs in explicit dependency order."""
    by_source = {entry.prod: entry for entry in entries}
    return [by_source[name] for name in order_sync_sources([entry.prod for entry in entries])]


def select_sync_entries(
    entries: list[SyncReadinessEntry],
    *,
    source: str | None = None,
    target: str | None = None,
) -> list[SyncReadinessEntry]:
    """Select ready sync entries by source and/or target, in approved dependency order."""
    selected = entries
    if source:
        selected = [entry for entry in selected if entry.prod == source]
    if target:
        selected = [entry for entry in selected if entry.expected_dev == target]
    return order_sync_entries(selected)
