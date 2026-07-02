"""Session-scoped handoff snapshot to avoid rebuilding transfer bundles on every menu refresh."""

from __future__ import annotations

from dataclasses import dataclass

from mercury.core.runtime import should_probe_database_status
from mercury.handoff.checklist import HandoffChecklist, build_handoff_checklist_from_bundle
from mercury.transfer.bundle import TransferBundle, build_transfer_bundle


@dataclass(frozen=True)
class HandoffSnapshot:
    bundle: TransferBundle
    checklist: HandoffChecklist
    live: bool


_snapshot: HandoffSnapshot | None = None


def clear_handoff_snapshot() -> None:
    """Drop the cached handoff snapshot (after writes or explicit refresh)."""
    global _snapshot
    _snapshot = None


def build_handoff_snapshot(*, live: bool | None = None, refresh: bool = False) -> HandoffSnapshot:
    """
    Build or return a cached handoff snapshot for the current operator session.

    Reuses the transfer bundle between handoff menu redraws unless ``refresh`` is True.
    """
    global _snapshot
    use_live = should_probe_database_status() if live is None else live
    if not refresh and _snapshot is not None and _snapshot.live == use_live:
        return _snapshot
    bundle = build_transfer_bundle(live=use_live)
    checklist = build_handoff_checklist_from_bundle(bundle)
    _snapshot = HandoffSnapshot(bundle=bundle, checklist=checklist, live=use_live)
    return _snapshot
