"""Phase 2 Backup and Sync session orchestration (hermetic)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.backup.session_models import (
    LaneResult,
    SessionPlan,
    SessionResult,
    classify_session_result,
    recommended_session_plan,
)
from mercury.backup.session_receipt import (
    render_session_summary_text,
    write_host_local_session_refusal,
    write_session_receipt,
)
from mercury.backup.session_runner import SessionHooks, preview_session, run_backup_sync_session
from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    load_host_maintenance,
    save_host_maintenance,
)
from mercury.storage.operation_availability import AvailabilityClassification
from mercury.storage.transitions import RESTORE_SOURCE_WRITER_PHRASE, TransitionStatus


@pytest.fixture
def host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(path))
    monkeypatch.setenv("MERCURY_TRANSITION_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("MERCURY_TEST_ISOLATION", "1")
    monkeypatch.delenv("MERCURY_ACTIVE_OPERATION", raising=False)
    return path


def _enabled_host(path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
            destination_rehearsal_active=False,
            source_detach_preparation=False,
        ),
        path=path,
    )


def _fake_batch(*, databases: list[str], executed: bool = True, refused: bool = False):
    results = []
    for name in databases:
        results.append(
            SimpleNamespace(
                database=name,
                executed=executed and not refused,
                refused=refused,
                error=None,
                backup_directory_path=f"/tmp/fake/{name}",
                backup_directory=f"/tmp/fake/{name}",
                manifest=SimpleNamespace(
                    backup_id=f"{name}-full-20260723T000000",
                    sha256="a" * 64,
                    created_at="2026-07-23T00:00:00Z",
                ),
            )
        )
    return SimpleNamespace(
        results=results,
        executed_count=len(results) if executed and not refused else 0,
        refused_count=len(results) if refused else 0,
        errors=[],
    )


def _fake_verify(batch, *, allow_development_backup: bool = False, fail: bool = False):
    ids = [
        item.manifest.backup_id
        for item in batch.results
        if getattr(item, "manifest", None) is not None
    ]
    return SimpleNamespace(
        verified=0 if fail else len(ids),
        failed=len(ids) if fail else 0,
        backup_ids=ids if not fail else [],
        issues=["verify failed"] if fail else [],
    )


def _fake_git(*, fail: bool = False, count: int = 2):
    entries = []
    for i in range(count):
        entries.append(
            SimpleNamespace(
                key=f"repo{i}",
                display_name=f"Repo {i}",
                commit="abc123",
                branch="main",
                planned_bundle_path=Path(f"/tmp/fake/repo{i}_20260723.bundle"),
                bundle_verified=not fail,
                executed=not fail,
                dirty=False,
                error="boom" if fail else None,
            )
        )
    return SimpleNamespace(entries=entries)


def _fake_sync(*, fail: bool = False):
    return SimpleNamespace(
        results=[
            SimpleNamespace(
                source="erebus_threat_intel_prod",
                target="erebus_threat_intel_dev",
                executed=not fail,
                refused=fail,
                verification_passed=None if fail else True,
                message="ok" if not fail else "refused",
            )
        ],
        executed_count=0 if fail else 1,
        refused_count=1 if fail else 0,
    )


def _hooks(
    *,
    prod=None,
    dev=None,
    verify=None,
    git=None,
    sync=None,
    restore_check=None,
    receipt=None,
    ensure=None,
    mark_source_delta=None,
    calls: dict | None = None,
) -> SessionHooks:
    tracker = calls if calls is not None else {}

    def track(name, fn):
        def wrapped(*a, **k):
            tracker[name] = tracker.get(name, 0) + 1
            return fn(*a, **k)

        return wrapped

    return SessionHooks(
        ensure_writes=ensure
        or (
            lambda **kwargs: SimpleNamespace(
                available=True,
                classification=AvailabilityClassification.AVAILABLE,
                transition_id="",
                transition_status=TransitionStatus.ALREADY_SATISFIED,
                blockers=(),
                operation_status=SimpleNamespace(value="READY"),
            )
        ),
        run_production_backup=track(
            "prod", prod or (lambda **k: _fake_batch(databases=["android_permission_intel"]))
        ),
        run_development_backup=track(
            "dev",
            dev or (lambda **k: _fake_batch(databases=["erebus_threat_intel_dev"])),
        ),
        verify_batch=track("verify", verify or _fake_verify),
        run_git_capture=track("git", git or (lambda: _fake_git())),
        run_sync=track("sync", sync or (lambda: _fake_sync())),
        run_restore_check=track(
            "restore_check",
            restore_check
            or (
                lambda **k: []
            ),
        ),
        write_receipt=track(
            "receipt",
            receipt
            or (
                lambda session: Path("/tmp/fake_receipt.json")  # overridden in tests with tmp
            ),
        ),
        mark_source_delta=mark_source_delta
        or (lambda **kwargs: load_host_maintenance()),
    )


def test_recommended_session_defaults() -> None:
    plan = recommended_session_plan()
    assert plan.production_backup is True
    assert plan.verify_production is True
    assert plan.git_recovery is True
    assert plan.development_backup is False
    assert plan.sync_development is False
    assert plan.restore_check is False


def test_writer_already_enabled(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"

    def write_receipt(session):
        return write_session_receipt(session, control_root=receipt_dir, require_active_operator_mount=False)

    calls: dict[str, int] = {}
    hooks = _hooks(receipt=write_receipt, calls=calls)
    session = run_backup_sync_session(
        recommended_session_plan(),
        execute=True,
        interactive=False,
        hooks=hooks,
    )
    assert session.session_result == SessionResult.PASS
    assert calls.get("prod") == 1
    assert calls.get("git") == 1
    assert calls.get("verify") == 1
    assert calls.get("prod") == 1  # exactly once


def test_strong_confirmation_required_for_live_like(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            destination_rehearsal_planned=True,
            package_id="destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: SimpleNamespace(
            identity=SimpleNamespace(
                uuid="715f29a9-2671-477b-8c8d-515d190addb9",
                label="MERCURY_DATA_V2",
                fstype="ext4",
                mountpoint="/mnt/MERCURY_DATA_V2",
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
    from mercury.storage.operation_availability import assess_operation_availability

    avail = assess_operation_availability("database_backup")
    assert avail.classification == AvailabilityClassification.STRONG_CONFIRMATION
    assert avail.confirmation_phrase == RESTORE_SOURCE_WRITER_PHRASE

    session = run_backup_sync_session(
        execute=True,
        interactive=False,
        hooks=_hooks(),
    )
    assert session.session_result == SessionResult.REFUSED
    assert any("RESTORE SOURCE WRITER" in f or "confirm-restore" in f for f in session.failures)


def test_operator_cancellation_recoverable(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            notes="Preparing for safe disconnect",
        ),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: SimpleNamespace(
            identity=SimpleNamespace(
                uuid="715f29a9-2671-477b-8c8d-515d190addb9",
                label="MERCURY_DATA_V2",
                fstype="ext4",
                mountpoint="/mnt/MERCURY_DATA_V2",
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

    def ensure(**kwargs):
        from mercury.storage.operation_availability import OperationStatus

        return SimpleNamespace(
            available=False,
            classification=AvailabilityClassification.RECOVERABLE_CONFIRMATION,
            transition_id="",
            transition_status=TransitionStatus.CANCELLED,
            blockers=("operator declined writer restoration",),
            operation_status=OperationStatus.CANCELLED,
        )

    session = run_backup_sync_session(
        execute=True,
        interactive=True,
        hooks=_hooks(ensure=ensure),
    )
    assert session.session_result == SessionResult.CANCELLED


def test_hard_blocked_storage(host_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detached", writes_allowed=False),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: SimpleNamespace(identity=None, errors=["absent"]),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.detect_desktop_automount",
        lambda *_a, **_k: [],
    )
    session = run_backup_sync_session(execute=True, interactive=False, hooks=_hooks())
    assert session.session_result == SessionResult.REFUSED
    assert session.receipt_result == "HOST_LOCAL_REFUSAL"


def test_production_failure_is_fail(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"

    def write_receipt(session):
        return write_session_receipt(
            session, control_root=receipt_dir, require_active_operator_mount=False
        )

    hooks = _hooks(
        prod=lambda **k: (_ for _ in ()).throw(RuntimeError("dump failed")),
        receipt=write_receipt,
    )
    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=True).normalize(),
        execute=True,
        interactive=False,
        hooks=hooks,
    )
    assert session.session_result == SessionResult.FAIL
    assert session.git_capture_result.requested is True


def test_verification_failure(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    hooks = _hooks(
        verify=lambda batch, **k: _fake_verify(batch, fail=True),
        receipt=lambda s: write_session_receipt(
            s, control_root=receipt_dir, require_active_operator_mount=False
        ),
    )
    session = run_backup_sync_session(
        recommended_session_plan(),
        execute=True,
        interactive=False,
        hooks=hooks,
    )
    assert session.session_result == SessionResult.FAIL
    assert session.verification_result.result == LaneResult.FAIL


def test_dev_accepted_and_declined(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    write = lambda s: write_session_receipt(
        s, control_root=receipt_dir, require_active_operator_mount=False
    )
    accepted = run_backup_sync_session(
        SessionPlan(
            production_backup=True, development_backup=True, git_recovery=False
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(receipt=write),
    )
    assert accepted.development_backup_result.requested is True
    assert accepted.development_backup_result.result == LaneResult.PASS

    declined = run_backup_sync_session(
        SessionPlan(
            production_backup=True, development_backup=False, git_recovery=False
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(receipt=write),
    )
    assert declined.development_backup_result.result == LaneResult.SKIPPED


def test_dev_failure_gives_partial(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    hooks = _hooks(
        dev=lambda **k: (_ for _ in ()).throw(RuntimeError("dev fail")),
        receipt=lambda s: write_session_receipt(
            s, control_root=receipt_dir, require_active_operator_mount=False
        ),
    )
    session = run_backup_sync_session(
        SessionPlan(
            production_backup=True, development_backup=True, git_recovery=False
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=hooks,
    )
    assert session.session_result == SessionResult.PARTIAL


def test_git_failure_gives_fail(host_path: Path, tmp_path: Path) -> None:
    """Required Git capture failure → FAIL (not merely PARTIAL)."""
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    hooks = _hooks(
        git=lambda: _fake_git(fail=True),
        receipt=lambda s: write_session_receipt(
            s, control_root=receipt_dir, require_active_operator_mount=False
        ),
    )
    session = run_backup_sync_session(
        recommended_session_plan(),
        execute=True,
        interactive=False,
        hooks=hooks,
    )
    assert session.session_result == SessionResult.FAIL
    assert session.production_backup_result.result == LaneResult.PASS
    assert session.git_capture_result.result == LaneResult.FAIL


def test_sync_skipped_after_production_failure(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    calls: dict[str, int] = {}
    hooks = _hooks(
        prod=lambda **k: (_ for _ in ()).throw(RuntimeError("prod fail")),
        receipt=lambda s: write_session_receipt(
            s, control_root=receipt_dir, require_active_operator_mount=False
        ),
        calls=calls,
    )
    session = run_backup_sync_session(
        SessionPlan(
            production_backup=True,
            git_recovery=False,
            sync_development=True,
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=hooks,
    )
    assert session.production_dev_sync_result.result == LaneResult.SKIPPED
    assert "sync" not in calls


def test_sync_success_and_failure(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    write = lambda s: write_session_receipt(
        s, control_root=receipt_dir, require_active_operator_mount=False
    )
    ok = run_backup_sync_session(
        SessionPlan(
            production_backup=True, git_recovery=False, sync_development=True
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(receipt=write),
    )
    assert ok.production_dev_sync_result.result == LaneResult.PASS
    assert ok.sync_artifacts

    bad = run_backup_sync_session(
        SessionPlan(
            production_backup=True, git_recovery=False, sync_development=True
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(sync=lambda: _fake_sync(fail=True), receipt=write),
    )
    assert bad.session_result == SessionResult.PARTIAL


def test_exact_backup_and_git_ids(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    session = run_backup_sync_session(
        recommended_session_plan(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            )
        ),
    )
    assert any("android_permission_intel-full-" in i for i in session.exact_artifact_ids)
    assert any(i.endswith(".bundle") for i in session.exact_artifact_ids)


def test_receipt_atomic_and_failure_prevents_pass(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=False).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            )
        ),
    )
    assert session.receipt_result == "WRITTEN"
    assert (receipt_dir / session.session_id / "session.json").is_file()
    assert (receipt_dir / session.session_id / "SHA256SUMS").is_file()

    failed = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=False).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(receipt=lambda s: (_ for _ in ()).throw(OSError("disk full"))),
    )
    assert failed.receipt_result == "FAILED"
    assert failed.session_result == SessionResult.PARTIAL


def test_refused_writes_no_hdd_receipt(host_path: Path, tmp_path: Path, monkeypatch) -> None:
    save_host_maintenance(
        HostMaintenanceState(storage_availability="detached", writes_allowed=False),
        path=host_path,
    )
    monkeypatch.setattr(
        "mercury.storage.block_device.resolve_mercury_block_device",
        lambda **kwargs: SimpleNamespace(identity=None, errors=["absent"]),
    )
    monkeypatch.setattr(
        "mercury.storage.detach_wizard.detect_desktop_automount",
        lambda *_a, **_k: [],
    )
    refusal_dir = tmp_path / "refused"
    monkeypatch.setenv("MERCURY_REFUSED_OPERATIONS_DIR", str(refusal_dir))
    session = run_backup_sync_session(execute=True, interactive=False, hooks=_hooks())
    assert session.session_result == SessionResult.REFUSED
    assert session.receipt_result == "HOST_LOCAL_REFUSAL"
    assert list(refusal_dir.glob("*_refused.json"))


def test_source_delta_first_db_and_git_write(host_path: Path, tmp_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
            package_id="phase3b_pkg",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            source_writes_resumed_after_package=True,
            source_delta_relative_to_package_id="phase3b_pkg",
            source_changed_since_package=False,
        ),
        path=host_path,
    )
    receipt_dir = tmp_path / "receipts"
    calls: list[tuple[str, str]] = []

    def mark(**kwargs):
        calls.append((kwargs.get("operation", ""), kwargs.get("artifact_id", "")))
        from mercury.storage.host_maintenance import mark_source_changed_since_package

        return mark_source_changed_since_package(**kwargs)

    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=True).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
        ),
    )
    # Default mark_source_delta in _hooks is no-op; override:
    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=False).normalize(),
        execute=True,
        interactive=False,
        hooks=SessionHooks(
            ensure_writes=lambda **k: SimpleNamespace(
                available=True,
                classification=AvailabilityClassification.AVAILABLE,
                transition_id="",
                transition_status=TransitionStatus.ALREADY_SATISFIED,
                blockers=(),
                operation_status=SimpleNamespace(value="READY"),
            ),
            run_production_backup=lambda **k: _fake_batch(
                databases=["android_permission_intel"]
            ),
            verify_batch=_fake_verify,
            run_git_capture=lambda: _fake_git(),
            write_receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
            mark_source_delta=mark,
        ),
    )
    assert calls
    assert calls[0][0] == "production_database_backup"
    host = load_host_maintenance(host_path)
    assert host.recovery_artifacts_created_after_package is True
    assert host.source_data_changed_since_package is False
    assert host.first_post_package_artifact_type == "database_backup"
    assert host.source_changed_since_package is True  # legacy mirror
    assert host.source_delta_first_write_operation in {
        "production_database_backup",
        "database_backup",
    }


def test_exactly_once_lane_invocation(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    calls: dict[str, int] = {}
    run_backup_sync_session(
        recommended_session_plan(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
            calls=calls,
        ),
    )
    assert calls["prod"] == 1
    assert calls["git"] == 1
    assert calls["verify"] == 1


def test_preview_and_menu_session_option() -> None:
    from mercury.backup.menu_options import (
        ACTION_BACKUP_SYNC_SESSION,
        ACTION_FULL_BACKUP,
        backup_menu_hint,
        backup_menu_render_options,
    )

    assert backup_menu_hint(ACTION_BACKUP_SYNC_SESSION).endswith("[1]")
    assert backup_menu_hint(ACTION_FULL_BACKUP).endswith("[2]")
    options = dict(backup_menu_render_options(writes_allowed=False))
    assert "Back up and sync this workstation" in options["1"]
    assert "unavailable" not in options["1"]
    assert "unavailable" in options["2"]


def test_cli_preview_no_prompts(monkeypatch, capsys) -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["backup", "session", "--preview", "--json"])
    assert result.exit_code in {0, 2}
    payload = json.loads(result.stdout)
    assert "session_id" in payload
    assert payload["preview"] is True


def test_phase3b_note_preserved(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=False).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            )
        ),
    )
    assert "20260722T055400Z_phase3b" in session.phase3b_separation_note
    assert "PASS" in render_session_summary_text(session) or session.session_result.value in {
        "PASS",
        "PARTIAL",
        "FAIL",
    }


def test_classify_session_result_rules() -> None:
    from mercury.backup.session_models import BackupSyncSession, LaneSummary, freeze_session_plan

    session = BackupSyncSession(session_id="x", session_result=SessionResult.REFUSED)
    assert classify_session_result(session) == SessionResult.REFUSED

    plan = SessionPlan(
        production_backup=True,
        git_recovery=True,
        git_recovery_required=True,
    ).normalize()
    session = BackupSyncSession(
        session_id="y",
        requested_operations=plan,
        frozen_plan=freeze_session_plan(plan),
        production_backup_result=LaneSummary(
            requested=True, required=True, attempted=True, result=LaneResult.PASS
        ),
        verification_result=LaneSummary(
            requested=True, required=True, attempted=True, result=LaneResult.PASS
        ),
        git_capture_result=LaneSummary(
            requested=True, required=True, attempted=True, result=LaneResult.FAIL
        ),
        receipt_result="WRITTEN",
    )
    assert classify_session_result(session) == SessionResult.FAIL


def test_recoverable_writer_restoration_continues(host_path: Path, tmp_path: Path) -> None:
    """Recoverable detach-prep: accept restore then run recommended lanes."""
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=False,
            notes="Preparing for safe disconnect",
        ),
        path=host_path,
    )
    receipt_dir = tmp_path / "receipts"
    calls: dict[str, int] = {}

    def ensure(**kwargs):
        from mercury.storage.operation_availability import OperationStatus

        assert kwargs.get("interactive") is False or kwargs.get("ask_yes_no") is not None
        save_host_maintenance(
            HostMaintenanceState(
                storage_availability="mounted",
                writes_allowed=True,
                active_write_role="primary",
                source_detach_preparation=False,
            ),
            path=host_path,
        )
        return SimpleNamespace(
            available=True,
            classification=AvailabilityClassification.RECOVERABLE_CONFIRMATION,
            transition_id="tr-recoverable",
            transition_status=TransitionStatus.SUCCESS,
            blockers=(),
            operation_status=OperationStatus.CONTINUED,
        )

    session = run_backup_sync_session(
        recommended_session_plan(),
        execute=True,
        interactive=False,
        accept_recoverable=True,
        hooks=_hooks(
            ensure=ensure,
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
            calls=calls,
        ),
    )
    assert session.session_result == SessionResult.PASS
    assert session.storage_transition.required is True
    assert session.storage_transition.transition_id == "tr-recoverable"
    assert calls.get("prod") == 1
    assert calls.get("git") == 1


def test_strong_confirmation_restoration_with_phrase(
    host_path: Path, tmp_path: Path, monkeypatch
) -> None:
    """Live-like destination rehearsal: exact phrase restores then continues."""
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            active_write_role="none",
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            destination_rehearsal_planned=True,
            package_id="destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=host_path,
    )
    receipt_dir = tmp_path / "receipts"

    def ensure(**kwargs):
        from mercury.storage.operation_availability import OperationStatus

        phrase_fn = kwargs.get("ask_phrase")
        assert phrase_fn is not None
        assert phrase_fn("") == RESTORE_SOURCE_WRITER_PHRASE
        save_host_maintenance(
            HostMaintenanceState(
                storage_availability="mounted",
                writes_allowed=True,
                active_write_role="primary",
                package_id="destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z",
                package_verification_status="DESTINATION_PACKAGE_VERIFIED",
                source_writes_resumed_after_package=True,
                source_delta_relative_to_package_id=(
                    "destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z"
                ),
                source_changed_since_package=False,
            ),
            path=host_path,
        )
        return SimpleNamespace(
            available=True,
            classification=AvailabilityClassification.STRONG_CONFIRMATION,
            transition_id="tr-strong",
            transition_status=TransitionStatus.SUCCESS,
            blockers=(),
            operation_status=OperationStatus.CONTINUED,
        )

    session = run_backup_sync_session(
        recommended_session_plan(),
        execute=True,
        interactive=False,
        confirm_restore_phrase=RESTORE_SOURCE_WRITER_PHRASE,
        hooks=_hooks(
            ensure=ensure,
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
        ),
    )
    assert session.session_result == SessionResult.PASS
    assert session.storage_transition.confirmation_class == (
        AvailabilityClassification.STRONG_CONFIRMATION.value
    )
    host = load_host_maintenance(host_path)
    assert host.package_id == (
        "destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z"
    )
    assert host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"


def test_source_delta_first_git_write_only(host_path: Path, tmp_path: Path) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
            package_id="phase3b_pkg",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            source_writes_resumed_after_package=True,
            source_delta_relative_to_package_id="phase3b_pkg",
            source_changed_since_package=False,
        ),
        path=host_path,
    )
    receipt_dir = tmp_path / "receipts"
    calls: list[str] = []

    def mark(**kwargs):
        calls.append(kwargs.get("operation", ""))
        from mercury.storage.host_maintenance import mark_source_changed_since_package

        return mark_source_changed_since_package(**kwargs)

    session = run_backup_sync_session(
        SessionPlan(
            production_backup=False,
            verify_production=False,
            git_recovery=True,
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=SessionHooks(
            ensure_writes=lambda **k: SimpleNamespace(
                available=True,
                classification=AvailabilityClassification.AVAILABLE,
                transition_id="",
                transition_status=TransitionStatus.ALREADY_SATISFIED,
                blockers=(),
                operation_status=SimpleNamespace(value="READY"),
            ),
            run_git_capture=lambda: _fake_git(),
            write_receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
            mark_source_delta=mark,
        ),
    )
    assert session.session_result == SessionResult.PASS
    assert calls == ["git_recovery_capture"]
    host = load_host_maintenance(host_path)
    assert host.recovery_artifacts_created_after_package is True
    assert host.source_data_changed_since_package is False
    assert host.first_post_package_artifact_type == "git_capture"
    assert host.source_delta_first_write_operation in {
        "git_recovery_capture",
        "git_capture",
    }
    assert host.source_changed_since_package is True


def test_end_of_session_disconnect_offer() -> None:
    from mercury.backup.session_models import BackupSyncSession
    from mercury.backup.session_wizard import offer_post_session_actions

    session = BackupSyncSession(
        session_id="x", session_result=SessionResult.PASS, recommended_next_action="safe_disconnect"
    )
    # Capture printed options without prompting: inject ask.
    import mercury.backup.session_wizard as wiz

    printed: list[str] = []
    orig_write = wiz.output.write

    def capture(msg=""):
        printed.append(str(msg))
        return orig_write(msg)

    wiz.output.write = capture  # type: ignore[method-assign]
    try:
        from mercury.menu import prompts as menu_prompts

        orig_ask = menu_prompts.ask
        menu_prompts.ask = lambda *_a, **_k: "1"  # type: ignore[assignment]
        try:
            assert offer_post_session_actions(session) == "safe_disconnect"
        finally:
            menu_prompts.ask = orig_ask  # type: ignore[assignment]
    finally:
        wiz.output.write = orig_write  # type: ignore[method-assign]
    assert any("Safely disconnect Mercury HDD" in line for line in printed)


def test_session_choice_menu_marks_recommended_and_uses_choice_prompt(
    monkeypatch: pytest.MonkeyPatch, host_path: Path
) -> None:
    from mercury.backup import session_wizard as wiz
    from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            destination_rehearsal_in_progress=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z",
        ),
        path=host_path,
    )
    printed: list[str] = []
    prompts: list[str] = []

    monkeypatch.setattr(
        wiz.output, "write", lambda msg="": printed.append(str(msg))
    )

    from mercury.menu import prompts as menu_prompts

    def capturing_ask(prompt: str) -> str:
        # Use the real ask path so Choice normalization is exercised.
        normalized = menu_prompts.ensure_choice_prompt(prompt)
        prompts.append(normalized)
        return "0"

    monkeypatch.setattr(menu_prompts, "ask", capturing_ask)
    monkeypatch.setattr(menu_prompts, "ensure_choice_prompt", menu_prompts.ensure_choice_prompt)
    summaries: list[str] = []
    monkeypatch.setattr(
        wiz.display_screen,
        "write_summary",
        lambda msg="": summaries.append(str(msg)),
    )
    monkeypatch.setattr(wiz.display_screen, "open_screen", lambda *_a, **_k: None)
    monkeypatch.setattr(wiz.display_screen, "write_fields", lambda *_a, **_k: None)
    monkeypatch.setattr(wiz.display_screen, "write_blank", lambda: None)

    wiz._print_overview(availability_classification="STRONG_CONFIRMATION")
    assert any("recovery artifacts that are not included" in line for line in summaries)
    assert any("remains valid for destination rehearsal" in line for line in summaries)
    assert not any("new source changes that are not included" in line for line in summaries)

    assert wiz._choice_menu() == "0"
    assert any(
        "Restore source writer and run recommended session   recommended" in line
        for line in printed
    )
    assert prompts and prompts[0].endswith(": ")
    assert "Choice:" in prompts[0].replace("\n", "")


def test_expert_database_only_menu_path_remains() -> None:
    from mercury.backup.menu_options import (
        ACTION_FULL_BACKUP,
        ACTION_PRODUCTION_BACKUP,
        backup_menu_hint,
        backup_menu_render_options,
    )

    options = dict(backup_menu_render_options(writes_allowed=True))
    assert "Run full database backup" in options["2"]
    assert "Back up production databases" in options["3"]
    assert backup_menu_hint(ACTION_FULL_BACKUP).endswith("[2]")
    assert backup_menu_hint(ACTION_PRODUCTION_BACKUP).endswith("[3]")


def test_cli_noninteractive_execute_no_prompts(monkeypatch, capsys) -> None:
    from typer.testing import CliRunner

    from mercury.cli import app

    # Preview path already covered; execute without lanes must fail closed (no prompts).
    runner = CliRunner()
    result = runner.invoke(app, ["backup", "session", "--execute", "--json"])
    assert result.exit_code != 0
    # Must not hang waiting for stdin; BadParameter about explicit selections.
    assert "explicit" in (result.stdout + result.stderr).lower() or result.exception


def test_package_and_phase3b_identity_unchanged_by_session(
    host_path: Path, tmp_path: Path
) -> None:
    from mercury.backup.session_models import BackupSyncSession

    pkg = "destination_rehearsal_20260722T055400Z_phase3b_20260722T193251Z"
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
            package_id=pkg,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            source_writes_resumed_after_package=True,
            source_delta_relative_to_package_id=pkg,
            source_changed_since_package=False,
        ),
        path=host_path,
    )
    receipt_dir = tmp_path / "receipts"
    before = load_host_maintenance(host_path)
    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=True).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
            mark_source_delta=lambda **kwargs: load_host_maintenance(host_path),
        ),
    )
    after = load_host_maintenance(host_path)
    assert after.package_id == before.package_id == pkg
    assert after.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
    assert "20260722T055400Z_phase3b" in session.phase3b_separation_note
    assert "20260722T055400Z_phase3b" in BackupSyncSession(session_id="n").phase3b_separation_note


def test_main_menu_routes_backup_to_session_when_writes_disabled(
    host_path: Path, monkeypatch
) -> None:
    from mercury.menu import loop as menu_loop
    from mercury.menu.actions import MenuAction
    from mercury.menu.options import MAIN_BACKUP_SYNC

    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            destination_rehearsal_active=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        ),
        path=host_path,
    )
    called: list[str] = []

    monkeypatch.setattr(
        "mercury.menu.task_menus.run_backup_sync_hub",
        lambda: called.append("session"),
    )
    monkeypatch.setattr(
        "mercury.menu.actions.resolve_menu_action",
        lambda _c: MenuAction(
            key="1",
            title="Back up and sync this workstation",
            action_id=MAIN_BACKUP_SYNC,
            runner=lambda: called.append("session"),
        ),
    )
    monkeypatch.setattr("mercury.logging.events.log_menu_action", lambda **_k: None)
    monkeypatch.setattr(
        "mercury.logging.log_operation",
        lambda *_a, **_k: __import__("contextlib").nullcontext(),
    )

    result = menu_loop.handle_menu_choice("1")
    assert result == "continue"
    assert called == ["session"]


def test_restore_check_lane_runs_exact_ids(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    seen: list[list[str]] = []

    def fake_rc(*, exact_backup_ids):
        seen.append(list(exact_backup_ids))
        from mercury.backup.session_models import RestoreCheckArtifactRecord

        return [
            RestoreCheckArtifactRecord(
                backup_id=bid,
                database="android_permission_intel",
                restore_schema=f"_restorecheck_{bid}",
                result="PASS",
            )
            for bid in exact_backup_ids
        ]

    session = run_backup_sync_session(
        SessionPlan(
            production_backup=True,
            git_recovery=False,
            restore_check=True,
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
            git=lambda: _fake_git(count=0),
        ),
    )
    # Inject restore-check hook via SessionHooks directly
    session = run_backup_sync_session(
        SessionPlan(
            production_backup=True,
            git_recovery=False,
            restore_check=True,
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=SessionHooks(
            ensure_writes=lambda **k: SimpleNamespace(
                available=True,
                classification=AvailabilityClassification.AVAILABLE,
                transition_id="",
                transition_status=TransitionStatus.ALREADY_SATISFIED,
                blockers=(),
                operation_status=SimpleNamespace(value="READY"),
            ),
            run_production_backup=lambda **k: _fake_batch(
                databases=["android_permission_intel"]
            ),
            verify_batch=_fake_verify,
            run_restore_check=fake_rc,
            write_receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
        ),
    )
    assert seen and seen[0] == ["android_permission_intel-full-20260723T000000"]
    assert session.restore_check_result.attempted is True
    assert session.restore_check_result.result == LaneResult.PASS
    assert session.database_artifacts[0].restore_check_status == "PASS"
    assert "latest" not in str(seen).lower()


def test_restore_check_optional_failure_is_partial(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    from mercury.backup.session_models import RestoreCheckArtifactRecord

    session = run_backup_sync_session(
        SessionPlan(
            production_backup=True,
            git_recovery=False,
            restore_check=True,
            restore_check_required=False,
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=SessionHooks(
            ensure_writes=lambda **k: SimpleNamespace(
                available=True,
                classification=AvailabilityClassification.AVAILABLE,
                transition_id="",
                transition_status=TransitionStatus.ALREADY_SATISFIED,
                blockers=(),
                operation_status=SimpleNamespace(value="READY"),
            ),
            run_production_backup=lambda **k: _fake_batch(
                databases=["android_permission_intel"]
            ),
            verify_batch=_fake_verify,
            run_restore_check=lambda **k: [
                RestoreCheckArtifactRecord(
                    backup_id="android_permission_intel-full-20260723T000000",
                    result="FAIL",
                    message="boom",
                )
            ],
            write_receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
        ),
    )
    assert session.restore_check_result.result == LaneResult.FAIL
    assert session.session_result == SessionResult.PARTIAL


def test_frozen_plan_sealed_before_lanes(host_path: Path, tmp_path: Path) -> None:
    _enabled_host(host_path)
    receipt_dir = tmp_path / "receipts"
    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=True).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(
            receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            )
        ),
    )
    assert session.frozen_plan is not None
    assert session.frozen_plan.plan_digest
    assert "production_backup" in session.frozen_plan.required_lanes
    assert "git_recovery" in session.frozen_plan.required_lanes
    assert session.frozen_plan.plan_id


def test_receipt_failure_preserves_artifacts_partial(
    host_path: Path, tmp_path: Path
) -> None:
    _enabled_host(host_path)

    def boom(_session):
        raise OSError("atomic rename failed")

    session = run_backup_sync_session(
        SessionPlan(production_backup=True, git_recovery=True).normalize(),
        execute=True,
        interactive=False,
        hooks=_hooks(receipt=boom),
    )
    assert session.database_artifacts
    assert session.git_artifacts
    assert session.receipt_result == "FAILED"
    assert session.session_result == SessionResult.PARTIAL
    assert session.artifacts_result in {"PASS", "PARTIAL"}


def test_sync_marks_development_state_not_source_data(
    host_path: Path, tmp_path: Path
) -> None:
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="mounted",
            writes_allowed=True,
            active_write_role="primary",
            package_id="phase3b_pkg",
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            source_writes_resumed_after_package=True,
            source_delta_relative_to_package_id="phase3b_pkg",
        ),
        path=host_path,
    )
    receipt_dir = tmp_path / "receipts"
    from mercury.storage.host_maintenance import (
        mark_development_state_changed_since_package,
        mark_recovery_artifact_after_package,
    )

    def mark_dev(**kwargs):
        return mark_development_state_changed_since_package(**kwargs)

    def mark_rec(**kwargs):
        return mark_recovery_artifact_after_package(**kwargs)

    session = run_backup_sync_session(
        SessionPlan(
            production_backup=True,
            git_recovery=False,
            sync_development=True,
        ).normalize(),
        execute=True,
        interactive=False,
        hooks=SessionHooks(
            ensure_writes=lambda **k: SimpleNamespace(
                available=True,
                classification=AvailabilityClassification.AVAILABLE,
                transition_id="",
                transition_status=TransitionStatus.ALREADY_SATISFIED,
                blockers=(),
                operation_status=SimpleNamespace(value="READY"),
            ),
            run_production_backup=lambda **k: _fake_batch(
                databases=["android_permission_intel"]
            ),
            verify_batch=_fake_verify,
            run_sync=lambda: _fake_sync(),
            write_receipt=lambda s: write_session_receipt(
                s, control_root=receipt_dir, require_active_operator_mount=False
            ),
            mark_recovery_artifact=mark_rec,
            mark_development_change=mark_dev,
        ),
    )
    host = load_host_maintenance(host_path)
    assert host.recovery_artifacts_created_after_package is True
    assert host.development_state_changed_since_package is True
    assert host.source_data_changed_since_package is False
    assert session.session_result == SessionResult.PASS


def test_session_code_has_no_latest_selection() -> None:
    from pathlib import Path as P

    root = P("src/mercury/backup")
    for name in (
        "session_runner.py",
        "session_models.py",
        "session_wizard.py",
        "session_receipt.py",
    ):
        text = (root / name).read_text(encoding="utf-8")
        for needle in ("find_latest", "latest_backup", "latest_capture"):
            assert needle not in text, f"{name} contains {needle}"
