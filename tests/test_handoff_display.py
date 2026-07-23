"""Tests for handoff display helpers."""

from __future__ import annotations

from mercury.handoff.checklist import HandoffChecklist, HandoffStep
from mercury.handoff.display import (
    handoff_dashboard_line,
    handoff_pipeline_line,
    receiver_handoff_steps,
    short_handoff_action,
    suggested_menu_choice,
)


def test_short_handoff_action_strips_prefixes() -> None:
    assert short_handoff_action("Handoff [4] backup") == "[4] backup"
    assert short_handoff_action("Handoff menu [8] transfer") == "[8] transfer"
    assert short_handoff_action("./run.sh transfer handoff") == "transfer handoff"
    assert (
        short_handoff_action("Handoff Tools [5] → Run Backup [2]")
        == "Run Backup [2]"
    )


def test_handoff_pipeline_line_aggregates_phase_statuses() -> None:
    checklist = HandoffChecklist(
        handoff_status="partial",
        database_package="partial",
        repository_package="partial",
        steps=[
            HandoffStep(step_key="usb_root", label="USB", status="ok", detail="mounted"),
            HandoffStep(step_key="backups_verified", label="Verify", status="ok", detail="ok"),
            HandoffStep(step_key="backup_freshness", label="Fresh", status="ok", detail="fresh"),
            HandoffStep(
                step_key="restore_checks",
                label="Restore-checks",
                status="warn",
                detail="1 of 1 not restore-checked",
            ),
            HandoffStep(step_key="repo_bundles", label="Repos", status="warn", detail="stale"),
            HandoffStep(step_key="transfer_package", label="Transfer", status="fail", detail="missing"),
        ],
    )
    line = handoff_pipeline_line(checklist)
    assert "[--] Backup" in line
    assert "[--] Repos" in line
    assert "[!!] Transfer" in line


def test_handoff_dashboard_line_reflects_backup_lane_state() -> None:
    assert "[--] backups verified" in handoff_dashboard_line(
        verified_count=4,
        source_count=4,
    )
    assert "[ok] ready" in handoff_dashboard_line(
        verified_count=4,
        source_count=4,
        latest_handoff_status="complete",
    )
    assert "[--] stale backups" in handoff_dashboard_line(
        verified_count=3,
        source_count=4,
        stale_count=1,
    )
    assert "last transfer" in handoff_dashboard_line(
        verified_count=4,
        source_count=4,
        latest_transfer_at="2 days ago",
    )
    assert "[--] ready with absent" in handoff_dashboard_line(
        verified_count=3,
        source_count=4,
        absent_count=1,
        latest_handoff_status="complete",
    )
    assert "backups verified (absent sources)" in handoff_dashboard_line(
        verified_count=3,
        source_count=4,
        absent_count=1,
    )


def test_handoff_wizard_plan_line_respects_start_phase() -> None:
    from mercury.handoff.display import handoff_wizard_plan_line

    checklist = HandoffChecklist(
        handoff_status="partial",
        database_package="partial",
        repository_package="partial",
        steps=[
            HandoffStep(step_key="backups_verified", label="Verify", status="ok", detail="ok"),
            HandoffStep(step_key="repo_bundles", label="Repos", status="warn", detail="stale"),
        ],
    )
    line = handoff_wizard_plan_line(checklist, start_phase="verify")
    assert "Verify" in line
    assert "Backup" not in line


def test_wizard_result_progress_line_marks_completed_phases() -> None:
    from mercury.handoff.display import wizard_progress_summary, wizard_result_progress_line
    from mercury.handoff.wizard import HandoffWizardPhaseResult, HandoffWizardResult

    result = HandoffWizardResult(
        phases=[
            HandoffWizardPhaseResult(phase="backup", status="skipped", summary="fresh"),
            HandoffWizardPhaseResult(phase="verify", status="ok", summary="verified"),
        ]
    )
    line = wizard_result_progress_line(result)
    assert "[--] Backup" in line
    assert "[ok] Verify" in line
    assert "[  ] Repo" in line
    assert wizard_progress_summary(result) == "1 OK · 1 skip · 2 phase(s) run"


def test_suggested_menu_choice_points_to_receiver_when_complete() -> None:
    from mercury.handoff.menu_options import ACTION_RECEIVER_GUIDE, handoff_menu_option_by_action

    checklist = HandoffChecklist(
        handoff_status="complete",
        database_package="complete",
        repository_package="complete",
        steps=[],
    )
    assert suggested_menu_choice(checklist) == handoff_menu_option_by_action(
        ACTION_RECEIVER_GUIDE
    )[0]


def test_suggested_menu_choice_reads_handoff_action_key() -> None:
    from mercury.handoff.menu_options import (
        ACTION_TOOLS,
        ACTION_TOOLS_TRANSFER,
        handoff_menu_option_by_action,
        handoff_nested_hint,
    )

    checklist = HandoffChecklist(
        handoff_status="partial",
        database_package="partial",
        repository_package="partial",
        steps=[
            HandoffStep(
                label="Transfer",
                status="fail",
                detail="missing",
                action=handoff_nested_hint(ACTION_TOOLS_TRANSFER),
            )
        ],
    )
    assert suggested_menu_choice(checklist) == handoff_menu_option_by_action(ACTION_TOOLS)[0]


def test_receiver_handoff_steps_include_deploy_guidance() -> None:
    steps = receiver_handoff_steps()
    by_label = {phase: status for phase, status, _detail in steps}
    assert "Import database backups" in by_label
    assert "Restore repository bundles" in by_label
    assert by_label["Run restore-check drills"] == "warn"
    assert by_label["Install Mercury on receiver"] == "info"
    assert by_label["Restore repository bundles"] == "info"
