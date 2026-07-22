"""Tests for full-backup orchestration, menu hints, and run receipts."""

from __future__ import annotations

from pathlib import Path

from mercury.backup.batch_runner import (
    BackupBatchResult,
    BatchVerificationSummary,
    FullBackupOutcome,
    UNEXPECTEDLY_SMALL_PRODUCTION_BYTES,
    build_full_backup_run_result,
    new_full_backup_run_id,
    small_production_backup_warning,
    verify_written_backup_batch,
    write_full_backup_run_receipt,
)
from mercury.backup.backup_runner import BackupExecutionResult
from mercury.backup.manifest import BackupManifest
from mercury.backup.menu_options import (
    ACTION_BUNDLE,
    ACTION_RESTORE_CHECK,
    ACTION_VERIFY,
    BACKUP_MENU_OPTIONS,
    backup_menu_hint,
    backup_menu_next_actions,
)
from mercury.backup.content_contract import BackupContentContract, BackupObjectInventory
from mercury.terminal.format import format_backup_id_display


def _manifest(database: str, backup_id: str, *, size: int = 100_000) -> BackupManifest:
    return BackupManifest(
        backup_id=backup_id,
        database=database,
        backup_kind="full",
        created_at="2026-07-22T14:10:40Z",
        dump_file=f"{database}.sql.gz",
        schema_file=f"{database}.schema.sql.gz",
        sha256="a" * 64,
        schema_sha256="b" * 64,
        size_bytes=size,
        schema_size_bytes=1000,
        source_role="production",
        tool_used="mariadb-dump",
        verified=False,
        live_actions_enabled=True,
        dry_run=False,
    )


def _executed(database: str, backup_id: str, directory: Path, *, size: int = 100_000) -> BackupExecutionResult:
    return BackupExecutionResult(
        database=database,
        backup_kind="full",
        dry_run=False,
        executed=True,
        refused=False,
        backup_directory=str(directory),
        backup_directory_path=str(directory),
        manifest=_manifest(database, backup_id, size=size),
        content_contract=BackupContentContract(
            live=BackupObjectInventory(tables=["t1"]),
            dump=BackupObjectInventory(tables=["t1"]),
            verified=True,
        ),
    )


def test_menu_hints_stay_synchronized_with_option_numbers() -> None:
    assert backup_menu_hint(ACTION_VERIFY) == "Verify source backups [4]"
    assert backup_menu_hint(ACTION_RESTORE_CHECK) == "Restore-check source backups [5]"
    assert backup_menu_hint(ACTION_BUNDLE) == "Write DB bundle and runbooks [6]"
    keys = {key for key, _label, action, _help in BACKUP_MENU_OPTIONS}
    assert keys == {"1", "2", "3", "4", "5", "6", "7", "8", "9"}
    assert " [3]" not in backup_menu_hint(ACTION_VERIFY)


def test_format_backup_id_preserves_prefix_and_suffix() -> None:
    backup_id = "android_permission_intel-full-20260722_141040_061"
    shown = format_backup_id_display(backup_id, max_len=32)
    assert shown.startswith("android_permission")
    assert shown.endswith("141040_061")
    assert "…" in shown
    assert format_backup_id_display(backup_id, max_len=200) == backup_id


def test_full_backup_pass_production_only(tmp_path: Path) -> None:
    directory = tmp_path / "android"
    directory.mkdir()
    batch = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel"],
        results=[
            _executed(
                "android_permission_intel",
                "android_permission_intel-full-20260722_141040_061",
                directory,
            )
        ],
        executed_count=1,
    )
    verification = BatchVerificationSummary(
        verified=1,
        backup_ids=["android_permission_intel-full-20260722_141040_061"],
        evidence_paths=[str(directory / "manifest.json")],
    )
    result = build_full_backup_run_result(
        run_id=new_full_backup_run_id(),
        started_at_utc="2026-07-22T14:10:40Z",
        production_batch=batch,
        production_verification=verification,
        development_requested=False,
    )
    assert result.outcome == FullBackupOutcome.PASS
    assert result.overall_verified == 1
    assert result.next_actions == backup_menu_next_actions(ACTION_RESTORE_CHECK, ACTION_BUNDLE)
    assert "Phase 3B" in result.phase3b_separation_note
    assert result.package_classification == "verified_routine"


def test_full_backup_partial_when_dev_fails(tmp_path: Path) -> None:
    prod_dir = tmp_path / "prod"
    prod_dir.mkdir()
    prod = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel"],
        results=[
            _executed(
                "android_permission_intel",
                "android_permission_intel-full-1",
                prod_dir,
            )
        ],
        executed_count=1,
    )
    prod_verify = BatchVerificationSummary(verified=1, backup_ids=["android_permission_intel-full-1"])
    dev = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["erebus_threat_intel_dev"],
        results=[],
        executed_count=0,
        errors=["erebus_threat_intel_dev: dump failed"],
    )
    result = build_full_backup_run_result(
        run_id="20260722T141040Z_full_backup",
        started_at_utc="2026-07-22T14:10:40Z",
        production_batch=prod,
        production_verification=prod_verify,
        development_batch=dev,
        development_verification=BatchVerificationSummary(failed=0),
        development_requested=True,
    )
    assert result.outcome == FullBackupOutcome.PARTIAL


def test_full_backup_fail_when_production_verify_fails(tmp_path: Path) -> None:
    directory = tmp_path / "android"
    directory.mkdir()
    batch = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel"],
        results=[
            _executed(
                "android_permission_intel",
                "android_permission_intel-full-1",
                directory,
            )
        ],
        executed_count=1,
    )
    verification = BatchVerificationSummary(
        verified=0,
        failed=1,
        issues=["android_permission_intel (android_permission_intel-full-1): checksum mismatch"],
    )
    result = build_full_backup_run_result(
        run_id="run",
        started_at_utc="2026-07-22T14:10:40Z",
        production_batch=batch,
        production_verification=verification,
    )
    assert result.outcome == FullBackupOutcome.FAIL
    assert result.next_actions == []


def test_full_backup_refused_when_nothing_written() -> None:
    batch = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel"],
        results=[],
        executed_count=0,
        refused_count=1,
        errors=["android_permission_intel: mount identity refused"],
    )
    result = build_full_backup_run_result(
        run_id="run",
        started_at_utc="2026-07-22T14:10:40Z",
        production_batch=batch,
        production_verification=None,
    )
    assert result.outcome == FullBackupOutcome.REFUSED


def test_verify_written_backup_batch_targets_exact_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "exact"
    directory.mkdir()
    batch = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel"],
        results=[
            _executed(
                "android_permission_intel",
                "android_permission_intel-full-EXPECTED",
                directory,
            )
        ],
        executed_count=1,
    )
    seen: list[str] = []

    def fake_verify(backup_dir, **kwargs):
        seen.append(str(backup_dir))
        from mercury.backup.verification import BackupVerificationResult

        return BackupVerificationResult(
            backup_id="android_permission_intel-full-EXPECTED",
            database="android_permission_intel",
            backup_kind="full",
            manifest_path=str(Path(backup_dir) / "manifest.json"),
            verified=True,
            preview_only=False,
        )

    monkeypatch.setattr("mercury.backup.verification.verify_backup_directory", fake_verify)
    summary = verify_written_backup_batch(batch)
    assert seen == [str(directory)]
    assert summary.verified == 1
    assert summary.backup_ids == ["android_permission_intel-full-EXPECTED"]


def test_write_full_backup_run_receipt_links_ids(tmp_path: Path) -> None:
    batch = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel"],
        results=[
            _executed(
                "android_permission_intel",
                "android_permission_intel-full-1",
                tmp_path / "d",
            )
        ],
        executed_count=1,
    )
    (tmp_path / "d").mkdir()
    result = build_full_backup_run_result(
        run_id="20260722T141040Z_full_backup",
        started_at_utc="2026-07-22T14:10:40Z",
        production_batch=batch,
        production_verification=BatchVerificationSummary(
            verified=1, backup_ids=["android_permission_intel-full-1"]
        ),
    )
    path = write_full_backup_run_receipt(result, control_root=tmp_path / ".mercury_control")
    text = path.read_text(encoding="utf-8")
    assert "android_permission_intel-full-1" in text
    assert "20260722T141040Z_full_backup" in text
    assert "Phase 3B" in text


def test_small_production_backup_warning() -> None:
    result = _executed(
        "obsidiandroid_core_prod",
        "obsidiandroid_core_prod-full-1",
        Path("/tmp/x"),
        size=UNEXPECTEDLY_SMALL_PRODUCTION_BYTES - 1,
    )
    warning = small_production_backup_warning(result)
    assert warning is not None
    assert "obsidiandroid_core_prod" in warning
    large = _executed(
        "obsidiandroid_core_prod",
        "obsidiandroid_core_prod-full-2",
        Path("/tmp/x"),
        size=UNEXPECTEDLY_SMALL_PRODUCTION_BYTES,
    )
    assert small_production_backup_warning(large) is None


def test_wait_for_continue_skips_when_non_interactive(monkeypatch) -> None:
    from mercury.menu import prompts as menu_prompts

    called = {"n": 0}

    def boom() -> None:
        called["n"] += 1
        raise AssertionError("should not block")

    monkeypatch.setattr(menu_prompts, "_continue_reader", None)
    monkeypatch.setattr(menu_prompts, "is_interactive_terminal", lambda: False)
    monkeypatch.setattr(menu_prompts, "ask_safe", boom)
    menu_prompts.wait_for_continue()
    assert called["n"] == 0


def test_print_batch_suggest_verify_defaults_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.terminal.batch import print_backup_batch_result

    batch = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel"],
        executed_count=1,
        results=[
            _executed(
                "android_permission_intel",
                "android_permission_intel-full-1",
                Path("/tmp/x"),
            )
        ],
    )
    print_backup_batch_result(batch, compact=True, menu=True)
    out = capsys.readouterr().out
    assert "Next: Verify source backups [4]" not in out

    print_backup_batch_result(batch, compact=True, menu=True, suggest_verify=True)
    out = capsys.readouterr().out
    assert "Next: Verify source backups [4]" in out
