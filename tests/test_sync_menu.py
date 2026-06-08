"""Tests for interactive sync menu."""

from __future__ import annotations

import pytest

from mercury.sync.interactive_menu import _blocked_prod_sources, _sync_submenu_options, run_sync_menu
from mercury.sync.readiness import SyncReadinessEntry, SyncReadinessReport


def _sample_report(*, ready: int = 0, blocked: int = 1) -> SyncReadinessReport:
    entries = [
        SyncReadinessEntry(
            prod="erebus_threat_intel_prod",
            expected_dev="erebus_threat_intel_dev",
            dev_listed=True,
            project="Erebus",
            ready_for_sync_planning=ready > 0,
            blockers=[] if ready > 0 else ["No on-disk backup found for production source."],
        ),
    ]
    return SyncReadinessReport(
        mode="live",
        backup_root="/tmp/backups",
        entries=entries,
        ready_count=ready,
        blocked_count=blocked,
    )


def test_blocked_prod_sources_skips_missing_dev_targets() -> None:
    report = _sample_report()
    assert _blocked_prod_sources(report) == ["erebus_threat_intel_prod"]


def test_sync_submenu_shows_prepare_when_blocked() -> None:
    report = _sample_report()
    labels = [label for _key, label in _sync_submenu_options(report)]
    assert any("Prepare production backups" in label for label in labels)
    assert any("live mode required" in label for label in labels)
    assert not any(label.startswith("Sync ready pairs") for label in labels)


def test_prepare_dry_run_shows_live_mode_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.batch_runner import BackupBatchResult
    from mercury.sync.interactive_menu import _prepare_production_backups

    monkeypatch.setattr(
        "mercury.sync.interactive_menu.run_backup_batch",
        lambda *args, **kwargs: BackupBatchResult(
            backup_kind="full",
            execute=False,
            sources=["erebus_threat_intel_prod"],
            dry_run_count=1,
        ),
    )
    _prepare_production_backups(_sample_report())
    out = capsys.readouterr().out
    assert "Result: dry-run only; no files were written." in out
    assert "Live mode guide" in out


def test_sync_submenu_shows_sync_when_ready() -> None:
    report = _sample_report(ready=1, blocked=1)
    labels = [label for _key, label in _sync_submenu_options(report)]
    assert any(label.startswith("Sync ready pairs") for label in labels)


def test_run_sync_menu_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("mercury.sync.interactive_menu._load_report", lambda: _sample_report())
    run_sync_menu(interactive=False)
    out = capsys.readouterr().out
    assert "Mode: LIVE" in out
    assert "0 ready, 1 blocked" in out
    assert "STATUS" in out
    assert "REASON" in out
    assert "PAIR" in out
    assert "blocked" in out
    assert "missing verified backup" in out
    assert "erebus_threat_intel_prod -> erebus_threat_intel_dev" in out
    assert "Rescan readiness" in out
    assert "Prepare production backups" in out
    assert "CLI:" not in out
    assert "[0] Return" not in out
    assert "Choose an action below" not in out
    assert "Choice:" not in out
    assert "Actions" not in out
    assert "backup-only and do not appear in prod-to-dev sync pairs" in out
    assert "╭" not in out
