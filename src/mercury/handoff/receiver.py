"""Receiving-workstation handoff guide (read-only)."""

from __future__ import annotations

from mercury.handoff.checklist import HandoffChecklist
from mercury.handoff.snapshot import build_handoff_snapshot
from mercury.core.runtime import should_probe_database_status


def build_receiver_handoff_guide(*, live: bool | None = None) -> HandoffChecklist | None:
    """
    Build receiver guidance from the current handoff snapshot when available.

    Returns the source checklist used to contextualize receiver steps, or None when
    the snapshot cannot be built.
    """
    use_live = should_probe_database_status() if live is None else live
    try:
        return build_handoff_snapshot(live=use_live, refresh=True).checklist
    except (OSError, ValueError):
        return None
