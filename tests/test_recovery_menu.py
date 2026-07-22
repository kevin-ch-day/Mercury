"""Tests for disaster recovery interactive menu."""

from __future__ import annotations

from mercury.handoff.checklist import HandoffChecklist, HandoffStep
from mercury.recovery.interactive_menu import RecoveryScreenData, _render_recovery_screen


def test_recovery_screen_shows_pipeline_and_receiver_options(
    monkeypatch,
    capsys,
) -> None:
    from mercury.backup.status import BackupStatusEntry, BackupStatusReport
    from mercury.handoff.snapshot import HandoffSnapshot

    report = BackupStatusReport(
        backup_root="/mnt/usb",
        backup_root_state="usb-mounted",
        source_count=1,
        verified_count=1,
        stale_count=0,
        missing_count=0,
        failed_count=0,
        unknown_freshness_count=0,
        entries=[
            BackupStatusEntry(
                database="erebus_threat_intel_prod",
                role="production",
                protection_status="verified",
                freshness="fresh",
                backup_created_at="2026-07-22T16:41:00+00:00",
                backup_id="erebus_threat_intel_prod-full-1",
                artifact_integrity_verified=True,
                manifest_verification_stamp=True,
                restore_check_status=None,
                handoff_eligible=False,
            )
        ],
    )
    checklist = HandoffChecklist(
        handoff_status="complete",
        database_package="complete",
        repository_package="complete",
        steps=[
            HandoffStep(step_key="transfer_package", label="Transfer", status="ok", detail="ok"),
        ],
    )
    monkeypatch.setattr(
        "mercury.recovery.interactive_menu.build_handoff_snapshot",
        lambda **kwargs: HandoffSnapshot(bundle=None, checklist=checklist, live=False),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "mercury.recovery.interactive_menu.resolve_operator_mount",
        lambda: __import__("pathlib").Path("/mnt/MERCURY_DATA_V2"),
    )
    _render_recovery_screen(
        RecoveryScreenData(
            report=report,
            restore_check_status={},
            latest_transfer_runbook=None,
            latest_database_runbook=None,
        ),
        show_title=True,
    )
    out = capsys.readouterr().out
    assert "Pipeline:" in out
    assert "Receiving workstation guide" in out
    assert "receiving-workstation guide" in out
    assert "FRESH" in out
    assert "ARTIFACT" in out
    assert "RC" in out
    assert "Verified" in out
    assert "None" in out
    assert "…ore-checked" not in out
    assert "Recovery gaps:" in out
    assert "not restore-checked" in out
    assert "baseline complete" not in out
