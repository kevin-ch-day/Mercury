"""Tests for disaster recovery interactive menu."""

from __future__ import annotations

from mercury.handoff.checklist import HandoffChecklist, HandoffStep
from mercury.recovery.interactive_menu import RecoveryScreenData, _render_recovery_screen


def test_recovery_screen_shows_pipeline_and_receiver_options(
    monkeypatch,
    capsys,
) -> None:
    from mercury.backup.status import BackupStatusReport
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
        entries=[],
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
