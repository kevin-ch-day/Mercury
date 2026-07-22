"""Terminal output for workstation handoff checklist."""

from __future__ import annotations

from mercury.handoff.checklist import HandoffChecklist, HandoffStep
from mercury.handoff.history import HandoffHistoryReport
from mercury.handoff.display import (
    HANDOFF_SCREEN_TITLE,
    HISTORY_SCREEN_TITLE,
    RECEIVER_SCREEN_TITLE,
    handoff_pipeline_line,
    handoff_status_kind,
    handoff_wizard_plan_line,
    primary_handoff_action,
    receiver_handoff_steps,
    receiver_quick_start_lines,
    short_handoff_action,
    step_progress_summary,
    step_status_label,
    wizard_progress_summary,
    wizard_result_progress_line,
)
from mercury.handoff.wizard import HandoffWizardResult, wizard_phase_label
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle


def _step_rows(steps: list[HandoffStep]) -> list[list[str]]:
    return [
        [
            step.label,
            step_status_label(step.status),
            step.detail,
            short_handoff_action(step.action),
        ]
        for step in steps
    ]


def print_handoff_checklist(checklist: HandoffChecklist) -> None:
    display_screen.open_screen(HANDOFF_SCREEN_TITLE)
    readiness_kind = handoff_status_kind(checklist.handoff_status)
    display_screen.write_status(
        readiness_kind,
        f"Handoff readiness: {checklist.handoff_status}",
    )
    display_screen.write_blank()
    fields = {
        "Pipeline": handoff_pipeline_line(checklist),
        "Checklist": step_progress_summary(checklist.steps),
        "Database package": checklist.database_package,
        "Repository package": checklist.repository_package,
        "Latest transfer on storage": checklist.latest_transfer_age or "none",
        "Latest DB bundle index": checklist.latest_database_bundle_age or "none",
        "State bundle records": checklist.state_bundle_rows,
    }
    if checklist.handoff_status != "complete":
        fields["Wizard plan"] = handoff_wizard_plan_line(checklist)
    display_screen.write_fields(fields)
    display_screen.write_blank()
    display_screen.write_section("Readiness checklist")
    rows = _step_rows(checklist.steps)
    if rows:
        display_screen.write_structured_table(
            Table.from_headers(
                ["STEP", "STATUS", "DETAIL", "NEXT"],
                rows,
                style=TableStyle(indent=0),
                min_col_widths=[22, 6, 28, 24],
            )
        )
    display_screen.write_blank()
    primary = primary_handoff_action(checklist)
    if checklist.handoff_status == "complete":
        display_screen.write_summary(
            "Operator storage is ready for workstation handoff — receiver should start with the latest transfer runbook."
        )
        display_screen.write_section("Receiver quick start")
        display_screen.write_list("", receiver_quick_start_lines())
    else:
        if primary:
            display_screen.write_hint(f"Recommended next: {primary}")
        display_screen.write_status(
            "warn",
            "Complete failed or warned steps before moving this media to another workstation.",
        )


def print_handoff_status_panel(checklist: HandoffChecklist) -> None:
    """Render the compact interactive-menu status panel without the full table."""
    display_screen.open_screen(HANDOFF_SCREEN_TITLE)
    display_screen.write_fields(
        {
            "Handoff readiness": checklist.handoff_status,
            "Database package": checklist.database_package,
            "Repository package": checklist.repository_package,
            "Latest transfer": checklist.latest_transfer_age or "none",
            "Checklist": step_progress_summary(checklist.steps),
        }
    )


def print_handoff_wizard_result(result: HandoffWizardResult) -> None:
    """Show phase-by-phase results from the guided handoff wizard."""
    if not result.phases:
        return
    display_screen.write_blank()
    display_screen.write_section("Handoff wizard progress")
    display_screen.write_fields(
        {
            "Phases": wizard_result_progress_line(result),
            "Summary": wizard_progress_summary(result),
        }
    )
    display_screen.write_blank()
    rows = [
        [wizard_phase_label(phase.phase), step_status_label(phase.status), phase.summary]
        for phase in result.phases
    ]
    display_screen.write_structured_table(
        Table.from_headers(
            ["PHASE", "RESULT", "SUMMARY"],
            rows,
            style=TableStyle(indent=0),
            min_col_widths=[18, 8, 40],
        )
    )
    detail_rows = [
        [wizard_phase_label(phase.phase), phase.detail]
        for phase in result.phases
        if phase.detail
    ]
    if detail_rows:
        display_screen.write_blank()
        display_screen.write_structured_table(
            Table.from_headers(
                ["PHASE", "DETAIL"],
                detail_rows,
                style=TableStyle(indent=0),
                min_col_widths=[18, 60],
            )
        )
    if result.final_handoff_status:
        display_screen.write_blank()
        readiness_kind = handoff_status_kind(result.final_handoff_status)
        display_screen.write_status(
            readiness_kind,
            f"Final handoff readiness: {result.final_handoff_status}",
        )
        if result.final_handoff_status == "complete":
            display_screen.write_summary(
                "Operator storage is ready for workstation handoff — receiver should start with the latest transfer runbook."
            )
        elif result.cancelled:
            display_screen.write_status("warn", "Guided wizard stopped before completion.")
        elif any(phase.status == "failed" for phase in result.phases):
            display_screen.write_status("warn", "One or more wizard phases failed — review steps above.")


def print_handoff_history(report: HandoffHistoryReport) -> None:
    """Show recent handoff-related ledger events."""
    from mercury.handoff.history import _display_timestamp

    display_screen.open_screen(HISTORY_SCREEN_TITLE)
    display_screen.write_fields(
        {
            "Transfer packages on storage": report.transfer_package_count,
            "Database bundle records": report.database_bundle_count,
            "Guided wizard runs": report.wizard_run_count,
        }
    )
    display_screen.write_blank()
    rows = [
        [
            _display_timestamp(entry.timestamp),
            entry.event,
            entry.handoff_status,
            entry.detail,
        ]
        for entry in report.entries
    ]
    if rows:
        display_screen.write_section("Recent events")
        display_screen.write_structured_table(
            Table.from_headers(
                ["WHEN", "EVENT", "STATUS", "DETAIL"],
                rows,
                style=TableStyle(indent=0),
                min_col_widths=[18, 16, 14, 36],
            )
        )
    else:
        display_screen.write_summary(
            "No handoff history on operator storage yet — run the guided wizard or write transfer/database bundles."
        )


def print_receiver_handoff_guide(*, checklist: HandoffChecklist | None = None) -> None:
    """Show the receiving-workstation checklist for imported handoff media."""
    display_screen.open_screen(RECEIVER_SCREEN_TITLE)
    if checklist is None:
        display_screen.write_status(
            "warn",
            "Handoff snapshot unavailable — follow the generic receiver checklist below.",
        )
    else:
        readiness_kind = handoff_status_kind(checklist.handoff_status)
        display_screen.write_status(
            readiness_kind,
            f"Source handoff status: {checklist.handoff_status}",
        )
        display_screen.write_blank()
        display_screen.write_fields(
            {
                "Pipeline": handoff_pipeline_line(checklist),
                "Latest transfer on storage": checklist.latest_transfer_age or "none",
                "Latest DB bundle index": checklist.latest_database_bundle_age or "none",
            }
        )
    display_screen.write_blank()
    display_screen.write_section("Receiver checklist")
    rows = [
        [phase, step_status_label(status), detail]
        for phase, status, detail in receiver_handoff_steps(checklist=checklist)
    ]
    display_screen.write_structured_table(
        Table.from_headers(
            ["PHASE", "STATUS", "DETAIL"],
            rows,
            style=TableStyle(indent=0),
            min_col_widths=[22, 8, 44],
        )
    )
    display_screen.write_blank()
    display_screen.write_section("Receiver commands")
    display_screen.write_list(
        "",
        [
            "./run.sh config init",
            "./run.sh doctor",
            "./run.sh deploy system",
            "./run.sh deploy dev --dry-run  # optional development recovery databases",
            "./run.sh deploy repos --from-usb",
            "./run.sh restore-check readiness --live",
        ],
    )
