"""Tests for handoff snapshot caching."""

from __future__ import annotations

import pytest

from mercury.handoff.snapshot import build_handoff_snapshot, clear_handoff_snapshot
from mercury.transfer.bundle import TransferBundle


def test_handoff_snapshot_reuses_cached_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def _fake_bundle(*, live: bool = False) -> TransferBundle:
        calls["count"] += 1
        return TransferBundle(
            generated_at="2026-07-02T00:00:00+00:00",
            host="fedora",
            mode="seed",
            backup_root="/tmp/backups",
            required_usb_mount="/tmp/usb",
            manifest_dir="/tmp/manifests",
            runbook_dir="/tmp/runbooks",
            transfer_manifest_path="/tmp/manifests/transfer_manifest_new.json",
            transfer_runbook_path="/tmp/runbooks/transfer_runbook_new.md",
        )

    monkeypatch.setattr("mercury.handoff.snapshot.build_transfer_bundle", _fake_bundle)
    monkeypatch.setattr(
        "mercury.handoff.snapshot.build_handoff_checklist_from_bundle",
        lambda bundle, **kwargs: type(
            "Checklist",
            (),
            {"handoff_status": "partial", "recommended_actions": lambda self: []},
        )(),
    )
    clear_handoff_snapshot()

    first = build_handoff_snapshot(live=False, refresh=False)
    second = build_handoff_snapshot(live=False, refresh=False)
    assert calls["count"] == 1
    assert first.bundle is second.bundle

    refreshed = build_handoff_snapshot(live=False, refresh=True)
    assert calls["count"] == 2
    assert refreshed.bundle is not first.bundle


def test_clear_handoff_snapshot_forces_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def _fake_bundle(*, live: bool = False) -> TransferBundle:
        calls["count"] += 1
        return TransferBundle(
            generated_at="2026-07-02T00:00:00+00:00",
            host="fedora",
            mode="seed",
            backup_root="/tmp/backups",
            required_usb_mount="/tmp/usb",
            manifest_dir="/tmp/manifests",
            runbook_dir="/tmp/runbooks",
            transfer_manifest_path="/tmp/manifests/transfer_manifest_new.json",
            transfer_runbook_path="/tmp/runbooks/transfer_runbook_new.md",
        )

    monkeypatch.setattr("mercury.handoff.snapshot.build_transfer_bundle", _fake_bundle)
    monkeypatch.setattr(
        "mercury.handoff.snapshot.build_handoff_checklist_from_bundle",
        lambda bundle, **kwargs: type(
            "Checklist",
            (),
            {"handoff_status": "partial", "recommended_actions": lambda self: []},
        )(),
    )
    clear_handoff_snapshot()
    build_handoff_snapshot(live=False)
    clear_handoff_snapshot()
    build_handoff_snapshot(live=False)
    assert calls["count"] == 2
