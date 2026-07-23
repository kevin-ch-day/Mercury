"""Tests for workstation handoff checklist."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.handoff.checklist import build_handoff_checklist
from mercury.transfer.bundle import TransferBundle, TransferDatabaseEntry


def test_build_handoff_checklist_marks_stale_backups_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "transfer_manifest_old.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "mercury.handoff.checklist.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": tmp_path / "backups",
                "backup_root_state": lambda self=None: "usb-mounted",
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.handoff.checklist.build_state_summary",
        lambda **kwargs: type(
            "Summary",
            (),
            {"database_bundle_rows": 0},
        )(),
    )
    monkeypatch.setattr(
        "mercury.handoff.checklist.build_transfer_bundle",
        lambda live=False: TransferBundle(
            generated_at="2026-07-02T00:00:00+00:00",
            host="fedora",
            mode="live",
            backup_root=str(tmp_path / "backups"),
            required_usb_mount=str(tmp_path),
            manifest_dir=str(manifest_dir),
            runbook_dir=str(tmp_path / "runbooks"),
            database_entries=[
                TransferDatabaseEntry(
                    database="erebus_threat_intel_prod",
                    source_role="production source",
                    verified=True,
                    freshness="stale",
                    backup_id="erebus-full-1",
                    backup_directory=str(tmp_path / "backups" / "erebus"),
                )
            ],
            verified_source_count=1,
            missing_source_count=0,
            failed_source_count=0,
            stale_source_count=1,
            unknown_freshness_source_count=0,
            transfer_manifest_path=str(manifest_dir / "transfer_manifest_new.json"),
            transfer_runbook_path=str(tmp_path / "runbooks" / "transfer_runbook_new.md"),
            latest_transfer_manifest_path=str(manifest_dir / "transfer_manifest_old.json"),
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.status.latest_restore_check_by_backup_id",
        lambda: {},
    )

    checklist = build_handoff_checklist(live=True)
    assert checklist.handoff_status == "blocked · 1 failed check"
    labels = [step.label for step in checklist.steps]
    assert "Backup freshness" in labels
    assert "Restore-checks" in labels
    restore_step = next(step for step in checklist.steps if step.step_key == "restore_checks")
    assert restore_step.status == "warn"
    assert "1 of 1 verified sources not restore-checked" in restore_step.detail


def test_restore_checks_warn_when_no_verified_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mercury.backup.status import RestoreCheckLedgerRecord

    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "mercury.handoff.checklist.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": tmp_path / "backups",
                "backup_root_state": lambda self=None: "usb-mounted",
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.handoff.checklist.build_state_summary",
        lambda **kwargs: type("Summary", (), {"database_bundle_rows": 0})(),
    )
    monkeypatch.setattr(
        "mercury.handoff.checklist.build_transfer_bundle",
        lambda live=False: TransferBundle(
            generated_at="2026-07-02T00:00:00+00:00",
            host="fedora",
            mode="live",
            backup_root=str(tmp_path / "backups"),
            required_usb_mount=str(tmp_path),
            manifest_dir=str(manifest_dir),
            runbook_dir=str(tmp_path / "runbooks"),
            database_entries=[
                TransferDatabaseEntry(
                    database="erebus_threat_intel_prod",
                    source_role="production source",
                    verified=False,
                    freshness=None,
                    backup_id=None,
                    backup_directory=None,
                )
            ],
            verified_source_count=0,
            missing_source_count=1,
            failed_source_count=0,
            stale_source_count=0,
            unknown_freshness_source_count=0,
            transfer_manifest_path=str(manifest_dir / "transfer_manifest_new.json"),
            transfer_runbook_path=str(tmp_path / "runbooks" / "transfer_runbook_new.md"),
            latest_transfer_manifest_path=None,
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.status.latest_restore_check_by_backup_id",
        lambda: {
            "ghost-full-1": RestoreCheckLedgerRecord(
                database="erebus_threat_intel_prod",
                backup_id="ghost-full-1",
                status="passed",
                timestamp="2026-07-22T06:00:00+00:00",
            )
        },
    )
    checklist = build_handoff_checklist(live=True)
    restore_step = next(step for step in checklist.steps if step.step_key == "restore_checks")
    assert restore_step.status == "warn"
    assert "No verified backup IDs" in restore_step.detail


def test_restore_checks_separates_failed_from_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mercury.backup.status import RestoreCheckLedgerRecord

    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "mercury.handoff.checklist.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": tmp_path / "backups",
                "backup_root_state": lambda self=None: "usb-mounted",
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.handoff.checklist.build_state_summary",
        lambda **kwargs: type("Summary", (), {"database_bundle_rows": 0})(),
    )
    monkeypatch.setattr(
        "mercury.handoff.checklist.build_transfer_bundle",
        lambda live=False: TransferBundle(
            generated_at="2026-07-02T00:00:00+00:00",
            host="fedora",
            mode="live",
            backup_root=str(tmp_path / "backups"),
            required_usb_mount=str(tmp_path),
            manifest_dir=str(manifest_dir),
            runbook_dir=str(tmp_path / "runbooks"),
            database_entries=[
                TransferDatabaseEntry(
                    database="erebus_threat_intel_prod",
                    source_role="production source",
                    verified=True,
                    freshness="fresh",
                    backup_id="erebus-full-1",
                    backup_directory=str(tmp_path / "backups" / "erebus"),
                ),
                TransferDatabaseEntry(
                    database="scytaledroid_core_prod",
                    source_role="production source",
                    verified=True,
                    freshness="fresh",
                    backup_id="scytale-full-1",
                    backup_directory=str(tmp_path / "backups" / "scytale"),
                ),
            ],
            verified_source_count=2,
            missing_source_count=0,
            failed_source_count=0,
            stale_source_count=0,
            unknown_freshness_source_count=0,
            transfer_manifest_path=str(manifest_dir / "transfer_manifest_new.json"),
            transfer_runbook_path=str(tmp_path / "runbooks" / "transfer_runbook_new.md"),
            latest_transfer_manifest_path=None,
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.status.latest_restore_check_by_backup_id",
        lambda: {
            "erebus-full-1": RestoreCheckLedgerRecord(
                database="erebus_threat_intel_prod",
                backup_id="erebus-full-1",
                status="failed",
                timestamp="2026-07-22T06:00:00+00:00",
            )
        },
    )
    checklist = build_handoff_checklist(live=True)
    restore_step = next(step for step in checklist.steps if step.step_key == "restore_checks")
    assert restore_step.status == "warn"
    assert "1 of 2 verified sources not restore-checked" in restore_step.detail
    assert "1 of 2 verified sources restore-check failed" in restore_step.detail


def test_handoff_aggregate_status_rules() -> None:
    from mercury.handoff.checklist import HandoffStep, aggregate_handoff_status

    assert aggregate_handoff_status([HandoffStep(label="ok", status="ok", detail="")], package_status="complete") == "complete"
    assert aggregate_handoff_status([HandoffStep(label="warn", status="warn", detail="")], package_status="complete") == "complete with warnings"
    assert aggregate_handoff_status([HandoffStep(label="fail", status="fail", detail="")], package_status="complete") == "blocked · 1 failed check"
    assert aggregate_handoff_status([
        HandoffStep(label="a", status="fail", detail=""),
        HandoffStep(label="b", status="fail", detail=""),
    ], package_status="complete with warnings") == "blocked · 2 failed checks"


def test_print_handoff_checklist_shows_steps(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.handoff.checklist import HandoffChecklist, HandoffStep
    from mercury.handoff.terminal import print_handoff_checklist

    print_handoff_checklist(
        HandoffChecklist(
            handoff_status="partial",
            database_package="partial",
            repository_package="partial",
            steps=[
                HandoffStep(
                    label="Operator backup root",
                    status="ok",
                    detail="/mnt/usb",
                )
            ],
        )
    )
    out = capsys.readouterr().out
    assert "Workstation Handoff" in out
    assert "Handoff readiness" in out
    assert "Readiness checklist" in out
    assert "Operator backup root" in out


def test_compact_handoff_status_panel_omits_checklist_table(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mercury.handoff.checklist import HandoffChecklist, HandoffStep
    from mercury.handoff.terminal import print_handoff_status_panel
    from mercury.storage.host_maintenance import HostMaintenanceState, save_host_maintenance

    host_path = tmp_path / "host_maintenance.json"
    monkeypatch.setenv("MERCURY_HOST_MAINTENANCE_PATH", str(host_path))
    save_host_maintenance(
        HostMaintenanceState(
            storage_availability="detaching",
            writes_allowed=False,
            source_detach_preparation=True,
            package_verification_status="DESTINATION_PACKAGE_VERIFIED",
            package_id="destination_rehearsal_final_source_20260723T161213Z",
        ),
        path=host_path,
    )
    print_handoff_status_panel(
        HandoffChecklist(
            handoff_status="partial",
            database_package="partial",
            repository_package="partial",
            steps=[HandoffStep(label="Operator backup root", status="ok", detail="/mnt/usb")],
        )
    )
    out = capsys.readouterr().out
    assert "DESTINATION HANDOFF STATUS" in out
    assert "Package" in out
    assert "destination_rehearsal_final_source_20260723T161213Z" in out
    assert "Latest transfer" not in out
    assert "Readiness checklist" not in out
    assert "Operator backup root" not in out
    assert "Advanced handoff tools" in out


def test_handoff_action_menu_is_one_compact_block(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.handoff.interactive_menu import _render_handoff_options

    _render_handoff_options()
    out = capsys.readouterr().out
    assert "Guided flow" not in out
    assert "Individual phases" not in out
    assert "[5] Handoff Tools" in out
    assert out.count("[0] Back") == 1
    assert "[2] Build Migration Package" in out
    assert "[4] Receiver Guide" in out
    assert "USB to HDD Migration" not in out
    assert "stale or missing" not in out


def test_handoff_checklist_recommended_actions_deduplicates() -> None:
    from mercury.handoff.checklist import HandoffChecklist, HandoffStep

    checklist = HandoffChecklist(
        handoff_status="partial",
        database_package="partial",
        repository_package="partial",
        steps=[
            HandoffStep(
                label="A",
                status="fail",
                detail="x",
                action="./run.sh backup run --execute",
            ),
            HandoffStep(
                label="B",
                status="warn",
                detail="y",
                action="./run.sh backup run --execute",
            ),
            HandoffStep(
                label="C",
                status="ok",
                detail="z",
                action="ignored",
            ),
        ],
    )
    assert checklist.recommended_actions() == ["./run.sh backup run --execute"]
