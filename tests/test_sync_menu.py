"""Tests for interactive sync menu."""

from __future__ import annotations

from pathlib import Path

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
    if ready > 1:
        entries.append(
            SyncReadinessEntry(
                prod="scytaledroid_core_prod",
                expected_dev="scytaledroid_core_dev",
                dev_listed=True,
                project="ScytaleDroid",
                ready_for_sync_planning=True,
                blockers=[],
            )
        )
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
    assert not any(label.startswith("Sync all ready pairs") for label in labels)


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
    assert any(label.startswith("Sync All Ready Databases") for label in labels)


def test_sync_submenu_shows_single_pair_option_when_multiple_ready() -> None:
    report = _sample_report(ready=2, blocked=0)
    labels = [label for _key, label in _sync_submenu_options(report)]
    assert any(label.startswith("Sync All Ready Databases") for label in labels)
    assert any(label.startswith("Sync One Ready Pair") for label in labels)


def test_run_sync_menu_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("mercury.sync.interactive_menu._load_report", lambda: _sample_report())
    run_sync_menu(interactive=False)
    out = capsys.readouterr().out
    assert "Backup root:" in out
    assert "STATUS" in out
    assert "REASON" in out
    assert "DATABASE" in out
    assert "blocked" in out
    assert "missing verified backup" in out
    assert "erebus_threat_intel" in out
    assert "Recheck Database Sync Status" in out
    assert "Prepare production backups" in out
    assert "CLI:" not in out
    assert "[0] Return" not in out
    assert "Choose an action below" not in out
    assert "Choice:" not in out
    assert "Actions" not in out
    assert "╭" not in out


def test_run_sync_ready_shows_compact_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.core.execution_policy import ExecutionPolicy
    from mercury.sync.interactive_menu import _run_sync_for_ready

    monkeypatch.setattr(
        "mercury.sync.interactive_menu.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=False,
            live_actions_enabled=True,
            backup_root=Path("/tmp/backups"),
            config_path=Path("/tmp/local.toml"),
            allow_unsafe_backup_root=True,
        ),
    )
    monkeypatch.setattr(
        "mercury.sync.interactive_menu.menu_prompts.ask_confirmation_phrase",
        lambda *args, **kwargs: False,
    )

    _run_sync_for_ready(_sample_report(ready=2, blocked=0))
    out = capsys.readouterr().out
    assert "overwrite development targets only" in out
    assert "Sync cancelled." in out
