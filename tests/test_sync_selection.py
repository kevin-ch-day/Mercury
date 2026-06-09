"""Tests for sync pair selection helpers."""

from mercury.sync.readiness import SyncReadinessEntry
from mercury.sync.selection import select_sync_entries


def _entries() -> list[SyncReadinessEntry]:
    return [
        SyncReadinessEntry(
            prod="erebus_threat_intel_prod",
            expected_dev="erebus_threat_intel_dev",
            dev_listed=True,
            ready_for_sync_planning=True,
        ),
        SyncReadinessEntry(
            prod="scytaledroid_core_prod",
            expected_dev="scytaledroid_core_dev",
            dev_listed=True,
            ready_for_sync_planning=True,
        ),
    ]


def test_select_sync_entries_returns_all_without_filter() -> None:
    entries = _entries()
    assert select_sync_entries(entries) == entries


def test_select_sync_entries_filters_by_source() -> None:
    selected = select_sync_entries(_entries(), source="erebus_threat_intel_prod")
    assert len(selected) == 1
    assert selected[0].expected_dev == "erebus_threat_intel_dev"


def test_select_sync_entries_filters_by_target() -> None:
    selected = select_sync_entries(_entries(), target="scytaledroid_core_dev")
    assert len(selected) == 1
    assert selected[0].prod == "scytaledroid_core_prod"
