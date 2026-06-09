"""Terminal output for deployment plans."""

from __future__ import annotations

from mercury.deploy.models import DeploymentPlan
from mercury.deploy.target_status import target_status_label
from mercury.terminal import screen as display_screen


def _action_label(action: str) -> str:
    labels = {
        "SKIP": "SKIP",
        "CREATE_AND_IMPORT": "CREATE + IMPORT",
        "BLOCKED": "BLOCKED",
        "OVERWRITE_DROP": "DROP + CREATE + IMPORT",
    }
    return labels.get(action, action)


def print_deployment_plan(plan: DeploymentPlan, *, compact: bool = False) -> None:
    display_screen.write_section("DATABASE DEPLOYMENT PLAN")
    display_screen.write_fields(
        {
            "Target host": plan.hostname,
            "Target MariaDB user": plan.mariadb_user,
            "Mode": plan.mode.upper(),
            "Existing target policy": plan.existing_target_policy,
            "Overwrite enabled": "yes" if plan.overwrite_enabled else "no",
            "Drop enabled": "yes" if plan.drop_enabled else "no",
        }
    )

    if plan.summary_message:
        display_screen.write_status(
            "ok" if not plan.deployment_needed else "info",
            plan.summary_message,
        )

    if plan.candidates:
        display_screen.write_blank()
        display_screen.write_summary("Databases selected:")
        for index, candidate in enumerate(plan.candidates, start=1):
            status = target_status_label(candidate.target_status)
            display_screen.write_summary(
                f"  {index}. {candidate.target_database}\n"
                f"     backup: {candidate.dump_path}\n"
                f"     backup status: {'verified' if candidate.verified else 'unverified'}\n"
                f"     target status: {status}\n"
                f"     action: {_action_label(candidate.deploy_action)}"
            )
            if candidate.action_reason:
                tag = "warn" if candidate.deploy_action in {"SKIP", "BLOCKED"} else "info"
                display_screen.write_status(tag, f"     reason: {candidate.action_reason}")
    else:
        display_screen.write_status("warn", "No deployment candidates resolved.")

    if plan.planned_commands and not compact:
        display_screen.write_blank()
        display_screen.write_summary("Actions:")
        for command in plan.planned_commands:
            display_screen.write_summary(f"  - {command}")
    elif plan.candidates and not plan.planned_commands and not compact:
        display_screen.write_blank()
        display_screen.write_summary("Actions:")
        display_screen.write_summary("  (none — no CREATE + IMPORT actions planned)")

    if not compact and (plan.import_count or plan.skip_count or plan.block_count):
        display_screen.write_blank()
        display_screen.write_summary(
            f"Plan summary: {plan.import_count} import, "
            f"{plan.skip_count} skip, {plan.block_count} blocked"
        )

    for note in plan.safety_notes:
        display_screen.write_status("info", note)
    for warning in plan.warnings:
        display_screen.write_status("warn", warning)
    for blocker in plan.blockers:
        display_screen.write_status("fail", blocker)
