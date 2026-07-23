"""Tests for handoff terminal formatting."""

from __future__ import annotations

from mercury.handoff.checklist import HandoffChecklist, HandoffStep
from mercury.handoff.display import (
    handoff_status_kind,
    primary_handoff_action,
    step_progress_summary,
)


def test_handoff_status_kind_maps_complete_and_partial() -> None:
    assert handoff_status_kind("complete") == "ok"
    assert handoff_status_kind("complete with warnings") == "warn"
    assert handoff_status_kind("partial") == "fail"


def test_step_progress_summary_counts_statuses() -> None:
    summary = step_progress_summary(
        [
            HandoffStep(label="a", status="ok", detail="x"),
            HandoffStep(label="b", status="warn", detail="y"),
            HandoffStep(label="c", status="fail", detail="z"),
        ]
    )
    assert summary == "1 OK · 1 Warn · 1 Fail"


def test_primary_handoff_action_prefers_first_failed_step() -> None:
    from mercury.handoff.menu_options import ACTION_TOOLS_BACKUP, handoff_nested_hint

    backup_action = handoff_nested_hint(ACTION_TOOLS_BACKUP)
    checklist = HandoffChecklist(
        handoff_status="partial",
        database_package="partial",
        repository_package="partial",
        steps=[
            HandoffStep(
                label="USB",
                status="fail",
                detail="missing",
                action=backup_action,
            ),
            HandoffStep(
                label="Transfer",
                status="warn",
                detail="stale",
                action="later",
            ),
        ],
    )
    assert primary_handoff_action(checklist) == backup_action


def test_print_handoff_checklist_shows_progress_and_receiver_block(
    capsys,
) -> None:
    from mercury.handoff.terminal import print_handoff_checklist

    print_handoff_checklist(
        HandoffChecklist(
            handoff_status="complete",
            database_package="complete",
            repository_package="complete",
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
    assert "Pipeline:" in out
    assert "Checklist:" in out
    assert "Receiver quick start" in out


def test_print_handoff_wizard_result_shows_phase_rail(capsys) -> None:
    from mercury.handoff.terminal import print_handoff_wizard_result
    from mercury.handoff.wizard import HandoffWizardPhaseResult, HandoffWizardResult

    print_handoff_wizard_result(
        HandoffWizardResult(
            phases=[
                HandoffWizardPhaseResult(phase="backup", status="skipped", summary="fresh"),
                HandoffWizardPhaseResult(phase="verify", status="ok", summary="verified"),
            ],
            final_handoff_status="partial",
        )
    )
    out = capsys.readouterr().out
    assert "Handoff wizard progress" in out
    assert "Phases:" in out
    assert "Summary:" in out
    assert "[ok] Verify" in out
