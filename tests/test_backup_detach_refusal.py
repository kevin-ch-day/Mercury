"""Regression tests for detach-maintenance backup refusal (no real HDD writes)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.backup.batch_runner import (
    FullBackupOutcome,
    LaneResult,
    apply_full_backup_run_evidence,
    build_full_backup_global_refusal_result,
    write_full_backup_run_receipt,
    write_host_local_refusal_record,
)
from mercury.backup.menu_options import (
    DETACH_UNAVAILABLE_SUFFIX,
    backup_menu_render_options,
)
from mercury.backup.write_preflight import (
    assess_backup_write_preflight,
    is_governed_full_backup_receipt,
    is_host_local_refusal_record,
)
from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance


@pytest.fixture
def detach_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            destination_rehearsal_in_progress=True,
        ),
        path=path,
    )
    return path


@pytest.fixture
def refusal_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "refused_operations"
    monkeypatch.setenv("MERCURY_REFUSED_OPERATIONS_DIR", str(root))
    return root


def test_preflight_refuses_detach_maintenance(detach_host: Path) -> None:
    preflight = assess_backup_write_preflight()
    assert preflight.allowed is False
    assert preflight.is_detach_maintenance is True
    assert "detach" in preflight.reason.lower() or "writes" in preflight.reason.lower()


def test_full_backup_global_refusal_before_dev_prompt(
    detach_host: Path,
    refusal_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Declining strong restore must refuse before the development prompt."""
    from mercury.backup.interactive_menu import _run_full_backup
    from mercury.database.backup_planning import build_backup_plan
    from mercury.storage.transitions import RESTORE_SOURCE_WRITER_PHRASE

    prompts: list[str] = []

    def _answer_yes_no(prompt: str, default=False):
        prompts.append(prompt)
        if "development" in prompt.lower():
            raise AssertionError("development prompt must not appear during global refusal")
        return False

    def _answer_phrase(*_args, **_kwargs):
        prompts.append("phrase")
        return ""  # decline strong confirmation

    monkeypatch.setattr("mercury.menu.prompts.ask_yes_no", _answer_yes_no)
    monkeypatch.setattr("mercury.menu.prompts.ask", _answer_phrase)
    batch_calls: list[object] = []

    def _no_batch(*_args, **_kwargs):
        batch_calls.append(1)
        raise AssertionError("run_backup_batch must not be called during global refusal")

    monkeypatch.setattr("mercury.backup.interactive_menu.run_backup_batch", _no_batch)
    hdd = tmp_path / "mnt" / "MERCURY_DATA_V2"
    hdd.mkdir(parents=True)
    control = hdd / ".mercury_control"
    monkeypatch.setattr(
        "mercury.core.usb_mount.resolve_operator_mount",
        lambda **kwargs: hdd,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: SimpleNamespace(
            identity=SimpleNamespace(
                uuid="715f29a9-2671-477b-8c8d-515d190addb9",
                label="MERCURY_DATA_V2",
                fstype="ext4",
                mountpoint="/mnt/MERCURY_DATA_V2",
                parent_device="/dev/sdb",
                partition_device="/dev/sdb1",
            ),
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.detect_desktop_automount",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "mercury.storage.transitions._probe_mount_mode",
        lambda *_a, **_k: "read-write",
    )

    plan = build_backup_plan(["android_permission_intel"])
    result = _run_full_backup(plan)
    out = capsys.readouterr().out

    assert "phrase" in prompts
    assert not any("development" in p.lower() for p in prompts)
    assert batch_calls == []
    assert result is None
    assert "SOURCE WRITER RESTORE REQUIRES CONFIRMATION" in out
    assert RESTORE_SOURCE_WRITER_PHRASE in out
    assert "Backup cancelled. Mercury writes remain disabled." in out
    assert "Also back up configured development" not in out
    assert "Development recovery" not in out
    assert "REFUSED" in out
    assert "No backup state changed" in out
    runs = control / "full_backup_runs"
    assert not runs.exists() or not any(runs.glob("*.json"))
    audits = list(refusal_dir.glob("*_refused.json"))
    assert len(audits) == 1
    payload = json.loads(audits[0].read_text(encoding="utf-8"))
    assert is_host_local_refusal_record(payload)
    assert is_governed_full_backup_receipt(payload) is False
    from mercury.storage.host_maintenance import load_host_maintenance

    host = load_host_maintenance(path=detach_host)
    assert host.writes_allowed is False
    assert host.storage_availability == "detaching"


def test_write_full_backup_receipt_blocked_when_writes_disabled(
    detach_host: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = build_full_backup_global_refusal_result(
        run_id="20260722T000000Z_full_backup",
        started_at_utc="2026-07-22T00:00:00+00:00",
        reason="Mercury HDD detach maintenance is active",
    )
    monkeypatch.setattr(
        "mercury.core.usb_mount.resolve_operator_mount",
        lambda **kwargs: tmp_path / "hdd",
    )
    (tmp_path / "hdd").mkdir()
    with pytest.raises(RuntimeError, match="writes_allowed=false|global preflight"):
        write_full_backup_run_receipt(result)


def test_host_local_refusal_classification(tmp_path: Path, refusal_dir: Path) -> None:
    result = build_full_backup_global_refusal_result(
        run_id="20260722T000001Z_full_backup",
        started_at_utc="2026-07-22T00:00:00+00:00",
        reason="Mercury HDD detach maintenance is active",
    )
    path = write_host_local_refusal_record(result, refusal_root=refusal_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert is_host_local_refusal_record(payload)
    assert payload["not_backup_evidence"] is True
    assert payload["not_handoff_evidence"] is True
    sealed = apply_full_backup_run_evidence(result, receipt_path=path)
    assert sealed.run_evidence_result == LaneResult.NOT_WRITTEN
    assert sealed.refusal_audit_result == "RECORDED_HOST_LOCAL"


def test_existing_refused_receipt_not_governed_success() -> None:
    payload = {
        "run_id": "20260722T211549Z_full_backup",
        "outcome": "REFUSED",
        "overall_written": 0,
        "overall_verified": 0,
        "backup_artifacts_result": "FAIL",
        "verification_result": "SKIPPED",
        "package_classification": "routine_only",
    }
    assert is_governed_full_backup_receipt(payload) is False


def test_menu_marks_write_actions_unavailable(detach_host: Path) -> None:
    options = dict(backup_menu_render_options(writes_allowed=False))
    assert DETACH_UNAVAILABLE_SUFFIX in options["2"]
    assert DETACH_UNAVAILABLE_SUFFIX in options["3"]
    assert DETACH_UNAVAILABLE_SUFFIX in options["4"]
    assert DETACH_UNAVAILABLE_SUFFIX in options["5"]
    assert DETACH_UNAVAILABLE_SUFFIX in options["6"]
    assert DETACH_UNAVAILABLE_SUFFIX in options["9"]
    assert DETACH_UNAVAILABLE_SUFFIX not in options["1"]
    assert DETACH_UNAVAILABLE_SUFFIX not in options["7"]
    assert DETACH_UNAVAILABLE_SUFFIX not in options["8"]


def test_menu_write_actions_available_after_restore(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="attached",
            writes_allowed=True,
            active_write_role="primary",
            destination_rehearsal_in_progress=False,
        ),
        path=path,
    )
    options = dict(backup_menu_render_options(writes_allowed=True))
    assert DETACH_UNAVAILABLE_SUFFIX not in options["2"]
    assert options["2"] == "Run full backup now"
    assert assess_backup_write_preflight().allowed is True


def test_backup_screen_shows_write_disabled_state(
    detach_host: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from mercury.backup.interactive_menu import _render_backup_screen
    from mercury.database.backup_planning import build_backup_plan

    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_prod_dev_pairs",
        lambda names: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.latest_records_by_database",
        lambda listing: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_on_disk_backup_list",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_backup_status_report",
        lambda live=False: type(
            "Report", (), {"entries": [], "stale_count": 0, "unknown_freshness_count": 0, "warnings": []}
        )(),
    )
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": root,
                "backup_execution_allowed": lambda self=None: True,
                "backup_root_state": lambda self=None: "operator-mounted",
            },
        )(),
    )
    # Use real _storage_usage_fields with mocked disk usage via existing root
    plan = build_backup_plan(["android_permission_intel"])
    _render_backup_screen(plan, show_title=True)
    out = capsys.readouterr().out
    assert "Write state:" in out
    assert "disabled" in out
    assert "Backup actions:" in out
    assert "unavailable" in out
    assert DETACH_UNAVAILABLE_SUFFIX in out
    assert "Refresh" in out
    assert "Preview backup plan" in out


def test_destination_package_never_includes_refused_run_receipts() -> None:
    """Membership helpers must not treat refused-run JSON as governed backup evidence."""
    refused = {
        "run_id": "20260722T211549Z_full_backup",
        "outcome": "REFUSED",
        "overall_written": 0,
        "backup_artifacts_result": "FAIL",
    }
    host_local = {
        "evidence_class": "host_local_refusal",
        "not_backup_evidence": True,
        "not_handoff_evidence": True,
        "outcome": "REFUSED",
        "overall_written": 0,
    }
    assert is_governed_full_backup_receipt(refused) is False
    assert is_governed_full_backup_receipt(host_local) is False
    # Destination package code has no full_backup_runs membership today.
    from mercury.migration import destination_package as dp

    source = Path(dp.__file__).read_text(encoding="utf-8")
    assert "full_backup_runs" not in source


def test_cli_full_backup_exits_2_on_maintenance_refusal(
    detach_host: Path,
    refusal_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    monkeypatch.setattr(
        "mercury.backup.batch_runner.resolve_batch_sources",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("sources not needed")),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["backup", "full", "--execute"])
    assert result.exit_code == 2
    assert "HDD detach maintenance" in result.output
    assert "DATABASE" not in result.output or "RESULT" not in result.output


def test_resolve_log_dir_redirects_off_hdd_when_writes_disabled(
    detach_host: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.logging.config import resolve_log_dir

    host_logs = tmp_path / "detach_logs"
    monkeypatch.setenv("MERCURY_DETACH_LOG_DIR", str(host_logs))
    monkeypatch.delenv("MERCURY_LOG_DIR", raising=False)
    monkeypatch.setattr(
        "mercury.logging.config.load_mercury_section",
        lambda: {"log_dir": "/mnt/MERCURY_DATA_V2/mercury_logs"},
    )
    monkeypatch.setattr(
        "mercury.core.storage_roles.DEFAULT_PRIMARY_MOUNT",
        "/mnt/MERCURY_DATA_V2",
    )
    resolved = resolve_log_dir()
    assert resolved == host_logs.resolve()
    assert "MERCURY_DATA_V2" not in str(resolved)


def test_ledger_append_skips_hdd_when_writes_disabled(
    detach_host: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.state import ledger

    fake_mount = tmp_path / "fake_mnt"
    fake_mount.mkdir()
    hdd_state = fake_mount / "mercury_state"
    hdd_state.mkdir()

    class _Primary:
        mount_path = fake_mount

    class _Cfg:
        primary = _Primary()

    monkeypatch.setattr(
        "mercury.core.storage_roots.load_storage_config",
        lambda **_kwargs: _Cfg(),
    )
    root = ledger._append_operation("test_event", {"ok": True}, state_root=hdd_state)
    assert root is None
    assert not (hdd_state / "operations.jsonl").exists()


def test_manifest_stamp_refused_when_writes_disabled(
    detach_host: Path,
    tmp_path: Path,
) -> None:
    from mercury.backup.verification import verify_backup_directory
    import mercury.backup.verification as ver

    backup_dir = tmp_path / "db"
    backup_dir.mkdir()
    (backup_dir / "manifest.json").write_text("{}", encoding="utf-8")

    class FakeResult:
        verified = True
        manifest_path = str(backup_dir / "manifest.json")
        database = "android_permission_intel"
        backup_kind = "full"
        backup_id = "id"
        issues: list[str] = []

    original = ver.verify_backup_artifacts
    ver.verify_backup_artifacts = lambda *a, **k: FakeResult()  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="refused|writes_allowed"):
            verify_backup_directory(
                backup_dir,
                database="android_permission_intel",
                update_manifest=True,
            )
        assert (backup_dir / "manifest.json").read_text(encoding="utf-8") == "{}"
    finally:
        ver.verify_backup_artifacts = original  # type: ignore[assignment]


def test_handoff_verify_phase_refuses_detach(detach_host: Path) -> None:
    from mercury.handoff.wizard import run_handoff_verify_phase

    phase = run_handoff_verify_phase(execute=True)
    assert phase.status == "failed"
    assert "detach maintenance" in phase.summary.lower()


def test_database_bundle_write_refuses_detach(detach_host: Path, tmp_path: Path) -> None:
    from mercury.backup.bundle import DatabaseBundlePlan, write_database_bundle_plan

    plan = DatabaseBundlePlan(
        generated_at="2026-07-22T00:00:00Z",
        backup_root=tmp_path,
        manifest_dir=tmp_path / "manifests",
        runbook_dir=tmp_path / "runbooks",
        planned_index_manifest_path=tmp_path / "manifests" / "index.json",
        planned_index_runbook_path=tmp_path / "runbooks" / "index.md",
        source_count=0,
        verified_count=0,
        missing_count=0,
        failed_count=0,
        stale_count=0,
        unknown_freshness_count=0,
        absent_count=0,
        entries=[],
        warnings=[],
    )
    with pytest.raises(RuntimeError, match="refused|writes_allowed"):
        write_database_bundle_plan(plan)


def test_classify_invalid_maintenance_receipt(tmp_path: Path) -> None:
    from mercury.backup.full_backup_receipts import (
        INVALID_MAINTENANCE_CLASS,
        classify_full_backup_receipt_payload,
        plan_quarantine_invalid_full_backup_receipts,
    )

    payload = {
        "run_id": "20260722T211549Z_full_backup",
        "outcome": "REFUSED",
        "overall_written": 0,
        "backup_artifacts_result": "FAIL",
        "verification_result": "SKIPPED",
        "package_classification": "routine_only",
    }
    classified = classify_full_backup_receipt_payload(payload)
    assert classified.classification == INVALID_MAINTENANCE_CLASS
    assert classified.governed is False

    control = tmp_path / ".mercury_control"
    runs = control / "full_backup_runs"
    runs.mkdir(parents=True)
    path = runs / "20260722T211549Z_full_backup.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    # Also drop a governed-looking success for contrast
    (runs / "ok_run.json").write_text(
        json.dumps(
            {
                "run_id": "ok_run",
                "outcome": "PASS",
                "overall_written": 4,
                "backup_artifacts_result": "PASS",
            }
        ),
        encoding="utf-8",
    )
    plan = plan_quarantine_invalid_full_backup_receipts(tmp_path, control_root=control)
    assert plan.invalid_count == 1
    assert plan.governed_count == 1
    assert plan.quarantine_dir.name == "invalid_maintenance_receipts"


def test_path_is_under_primary_mount_helper(tmp_path: Path, monkeypatch) -> None:
    from mercury.storage.host_maintenance import path_is_under_primary_mount

    mount = tmp_path / "mnt"
    mount.mkdir()
    assert path_is_under_primary_mount(mount / "mercury_logs" / "a.log", mount=mount) is True
    assert path_is_under_primary_mount(tmp_path / "other" / "x", mount=mount) is False


def test_quarantine_execute_refuses_detach(detach_host: Path, tmp_path: Path) -> None:
    from mercury.core.storage_roots import default_storage_config
    from mercury.storage.migrate_quarantine import (
        QUARANTINE_CONFIRMATION_PHRASE,
        quarantine_migration_conflicts,
    )

    result = quarantine_migration_conflicts(
        execute=True,
        confirmation=QUARANTINE_CONFIRMATION_PHRASE,
        config=default_storage_config(),
    )
    assert result.executed is False
    assert any("writes_allowed=false" in b for b in result.blockers)


def test_record_verified_generation_refuses_detach(
    detach_host: Path, tmp_path: Path
) -> None:
    from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
    from mercury.core.storage_roots import StorageConfig, StorageRootConfig
    from mercury.migration.generation import PackageGeneration, record_verified_generation

    legacy = tmp_path / "usb"
    primary = tmp_path / "hdd"
    legacy.mkdir()
    primary.mkdir()
    config = StorageConfig(
        primary=StorageRootConfig(
            "primary", StorageRootRole.CANONICAL, "HDD", primary, "hdd", "ext4", True
        ),
        legacy=StorageRootConfig(
            "legacy", StorageRootRole.TRANSITION_SOURCE, "USB", legacy, "usb", "ext4", True
        ),
        active_write_role=StorageWriteRole.LEGACY,
        migration_state=MigrationState.VERIFIED,
    )
    generation = PackageGeneration(
        generation="testgen",
        observed_at="2026-07-22T00:00:00Z",
        durable_entries=0,
        durable_files=0,
        latest_package_timestamp=None,
    )
    with pytest.raises(RuntimeError, match="writes_allowed|refused"):
        record_verified_generation(generation, config=config)


def test_assert_operator_storage_path_refuses_detach(
    detach_host: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.core.usb_mount import assert_operator_storage_path

    primary = tmp_path / "primary"
    primary.mkdir()
    monkeypatch.setattr(
        "mercury.core.usb_mount.usb_mount_is_active", lambda _mount: True
    )
    with pytest.raises(RuntimeError, match="writes_allowed|refused"):
        assert_operator_storage_path(
            primary / "mercury_repo_backups" / "x.md",
            operator_mount=primary,
        )


def test_progress_ledger_refuses_detach(detach_host: Path, tmp_path: Path) -> None:
    from mercury.storage.progress_ledger import ensure_ledger

    with pytest.raises(RuntimeError, match="writes_allowed|refused"):
        ensure_ledger(tmp_path / "primary")


def test_migrate_run_execute_refuses_detach(detach_host: Path, tmp_path: Path) -> None:
    from mercury.core.storage_roots import default_storage_config
    from mercury.storage.migrate_run import run_migration

    cfg = default_storage_config()
    result = run_migration(
        execute=True,
        confirmation="MIGRATE PRIMARY",
        update_state=False,
        config=cfg,
    )
    assert result.executed is False
    assert any("writes_allowed=false" in b for b in result.blockers)


def test_destination_documents_refuse_detach(detach_host: Path, tmp_path: Path) -> None:
    from mercury.migration.destination_documents import generate_destination_documents

    result = generate_destination_documents(tmp_path, documents_run="detach")
    assert result.ok is False
    assert any("writes_allowed=false" in e for e in result.errors)
    assert not (tmp_path / ".mercury_control" / "documents_runs").exists()


def test_cleanup_plan_write_under_primary_refuses_detach(
    detach_host: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.storage.cleanup import build_cleanup_preview

    mount = tmp_path / "primary"
    mount.mkdir()
    plan_path = mount / ".mercury_control" / "cleanup_plan.json"
    with pytest.raises(RuntimeError, match="writes_allowed|refused"):
        build_cleanup_preview(mount_root=mount, write_plan_path=plan_path)
    assert not plan_path.exists()


def test_handoff_tools_write_choice_refuses_before_phase(
    detach_host: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from mercury.handoff import interactive_menu as hm

    called: list[str] = []

    def _boom(*_a, **_k):
        called.append("phase")
        raise AssertionError("write phase must not run in detach mode")

    monkeypatch.setattr(hm, "read_submenu_choice", lambda: "2")
    monkeypatch.setattr(hm, "_render_handoff_tools", lambda: None)
    monkeypatch.setattr(hm, "run_handoff_backup_phase", _boom)
    hm._run_handoff_tools(snapshot=None)
    out = capsys.readouterr().out
    assert called == []
    assert "refused" in out.lower() or "detach" in out.lower()
