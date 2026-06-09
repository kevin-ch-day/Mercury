"""Terminal output for repository deployment plans."""

from __future__ import annotations

from mercury.deploy.repos.models import RepoDeployPlan
from mercury.terminal import screen as display_screen


def print_repo_deploy_plan(plan: RepoDeployPlan) -> None:
    display_screen.write_section("REPOSITORY DEPLOYMENT PLAN")
    display_screen.write_fields(
        {
            "Target host": plan.hostname,
            "Source mode": plan.source_mode,
            "Mode": plan.mode.upper(),
        }
    )
    if plan.candidates:
        display_screen.write_blank()
        display_screen.write_summary("Repositories:")
        for index, candidate in enumerate(plan.candidates, start=1):
            source_label = candidate.source.replace("_", " ")
            display_screen.write_summary(
                f"  {index}. {candidate.display_name}\n"
                f"     path: {candidate.target_path}\n"
                f"     source: {source_label}\n"
                f"     remote: {candidate.remote_url or 'n/a'}\n"
                f"     bundle: {candidate.bundle_path or 'n/a'}"
            )
            if candidate.skip_reason:
                display_screen.write_status("warn", f"     skip: {candidate.skip_reason}")
            if candidate.configured_path:
                display_screen.write_status("info", f"     configured: {candidate.configured_path}")

    if plan.planned_commands:
        display_screen.write_blank()
        display_screen.write_summary("Actions:")
        for command in plan.planned_commands:
            display_screen.write_summary(f"  - {command}")

    for note in plan.safety_notes:
        display_screen.write_status("info", note)
    for warning in plan.warnings:
        display_screen.write_status("warn", warning)
    for blocker in plan.blockers:
        display_screen.write_status("fail", blocker)
