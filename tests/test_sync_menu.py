"""Tests for interactive sync menu."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.execution_policy import ExecutionPolicy
from mercury.sync.interactive_menu import _blocked_prod_sources, _sync_submenu_options, run_sync_menu
from mercury.sync.readiness import SyncReadinessEntry, SyncReadinessReport


def _seed_execution_policy(tmp_path: Path) -> ExecutionPolicy:
    return ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "backups",
        config_path=None,
    )


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


def test_sync_submenu_shows_prepare_when_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mercury.sync.interactive_menu.load_execution_policy",
        lambda: _seed_execution_policy(tmp_path),
    )
    report = _sample_report()
    labels = [label for _key, label in _sync_submenu_options(report)]
    assert any("Prepare production backups" in label for label in labels)
    assert any("preview only" in label for label in labels)
    assert not any(label.startswith("Sync all ready pairs") for label in labels)


def test_prepare_dry_run_shows_live_mode_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from mercury.backup.batch_runner import BackupBatchResult
    from mercury.sync.interactive_menu import _prepare_production_backups

    monkeypatch.setattr(
        "mercury.sync.interactive_menu.load_execution_policy",
        lambda: _seed_execution_policy(tmp_path),
    )
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
    assert "Enable sync execution in config/local.toml" in out


def test_prepare_verifies_written_backup_ids(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from mercury.backup.batch_runner import BackupBatchResult, BatchVerificationSummary
    from mercury.core.execution_policy import ExecutionPolicy
    from mercury.sync.interactive_menu import _prepare_production_backups

    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path / "backups",
        config_path=None,
        allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr(
        "mercury.sync.interactive_menu.load_execution_policy",
        lambda: policy,
    )
    monkeypatch.setattr(
        ExecutionPolicy,
        "live_execution_allowed",
        lambda self: True,
    )
    monkeypatch.setattr(
        "mercury.sync.interactive_menu.run_backup_batch",
        lambda *args, **kwargs: BackupBatchResult(
            backup_kind="full",
            execute=True,
            sources=["erebus_threat_intel_prod"],
            executed_count=1,
        ),
    )
    called: list[object] = []

    def _verify(batch):
        called.append(batch)
        return BatchVerificationSummary(verified=1, failed=0, backup_ids=["erebus-1"])

    monkeypatch.setattr(
        "mercury.sync.interactive_menu.verify_written_backup_batch",
        _verify,
    )
    _prepare_production_backups(_sample_report())
    out = capsys.readouterr().out
    assert called
    assert "Verified 1 of 1 newly written backup ID(s)." in out


def test_sync_submenu_shows_sync_when_ready() -> None:
    report = _sample_report(ready=1, blocked=1)
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=Path("/tmp/backups"),
        config_path=None,
        allow_unsafe_backup_root=True,
    )
    import mercury.sync.interactive_menu as sync_menu

    original = sync_menu.load_execution_policy
    sync_menu.load_execution_policy = lambda: policy
    try:
        labels = [label for _key, label in _sync_submenu_options(report)]
    finally:
        sync_menu.load_execution_policy = original
    assert any(label.startswith("Preview All Ready Databases") for label in labels)


def test_sync_submenu_shows_single_pair_option_when_multiple_ready() -> None:
    report = _sample_report(ready=1, blocked=1)
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=Path("/tmp/backups"),
        config_path=None,
        allow_unsafe_backup_root=True,
    )
    import mercury.sync.interactive_menu as sync_menu

    original = sync_menu.load_execution_policy
    sync_menu.load_execution_policy = lambda: policy
    try:
        report = _sample_report(ready=2, blocked=0)
        labels = [label for _key, label in _sync_submenu_options(report)]
    finally:
        sync_menu.load_execution_policy = original
    assert any(label.startswith("Preview All Ready Databases") for label in labels)
    assert any(label.startswith("Preview One Ready Pair") for label in labels)


def test_run_sync_menu_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("mercury.sync.interactive_menu._load_report", lambda: _sample_report())
    monkeypatch.setattr(
        "mercury.sync.interactive_menu.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=Path("/tmp/backups"),
            config_path=None,
            allow_unsafe_backup_root=True,
        ),
    )
    run_sync_menu(interactive=False)
    out = capsys.readouterr().out
    assert "Backup root:" in out
    assert "erebus_threat_intel → erebus_threat_intel" in out
    assert "Pairs:" in out
    assert "PROD → DEV" in out
    assert "backup stale" in out or "missing backup" in out or "Run full backup" in out
    assert "blocked" in out
    assert "erebus_threat_intel" in out
    assert "Recheck Database Sync Status" in out
    assert "Prepare production backups" in out
    assert "preview only" in out.lower()
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
    assert "Prod→dev sync will overwrite these development databases" in out
    assert "erebus_threat_intel → erebus_threat_intel" in out
    assert "Sync cancelled." in out


def test_print_sync_batch_result_marks_preview_only(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.sync.sync_runner import SyncBatchResult, SyncExecutionResult
    from mercury.sync.terminal.runner import print_sync_batch_result

    batch = SyncBatchResult(
        results=[
            SyncExecutionResult(
                source="erebus_threat_intel_prod",
                target="erebus_threat_intel_dev",
                executed=False,
                dry_run=True,
                message="Would restore erebus_threat_intel_prod backup into erebus_threat_intel_dev.",
            )
        ]
    )
    print_sync_batch_result(batch, compact=True)
    out = capsys.readouterr().out
    assert "Preview only" in out
    assert "Would restore" not in out
