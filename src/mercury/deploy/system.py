"""Combined database + repository deployment planning."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.repos.build_plan import build_repo_deploy_plan
from mercury.deploy.target_status import target_status_label


class SystemDeployPlan(BaseModel):
    hostname: str
    mode: str = "dry-run"
    database_blockers: list[str] = Field(default_factory=list)
    repository_blockers: list[str] = Field(default_factory=list)
    database_commands: list[str] = Field(default_factory=list)
    repository_commands: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    database_deployment_needed: bool = True
    database_summary_message: str | None = None
    database_import_count: int = 0
    database_skip_count: int = 0
    database_candidates: list[dict[str, str]] = Field(default_factory=list)


def build_system_deploy_plan(*, execute: bool = False) -> SystemDeployPlan:
    db_plan = build_deployment_plan(execute=execute)
    repo_plan = build_repo_deploy_plan(execute=execute, source_mode="auto")
    warnings = list(db_plan.warnings) + list(repo_plan.warnings)
    db_candidates = [
        {
            "name": candidate.target_database,
            "target_status": target_status_label(candidate.target_status),
            "action": candidate.deploy_action.replace("_", " "),
        }
        for candidate in db_plan.candidates
    ]
    return SystemDeployPlan(
        hostname=db_plan.hostname,
        mode=db_plan.mode,
        database_blockers=list(db_plan.blockers),
        repository_blockers=list(repo_plan.blockers),
        database_commands=list(db_plan.planned_commands),
        repository_commands=list(repo_plan.planned_commands),
        warnings=warnings,
        database_deployment_needed=db_plan.deployment_needed,
        database_summary_message=db_plan.summary_message,
        database_import_count=db_plan.import_count,
        database_skip_count=db_plan.skip_count,
        database_candidates=db_candidates,
    )


def print_system_deploy_plan(plan: SystemDeployPlan) -> None:
    from mercury.terminal import screen as display_screen

    display_screen.write_section("SYSTEM DEPLOYMENT PLAN")
    display_screen.write_fields({"Target host": plan.hostname, "Mode": plan.mode.upper()})
    if plan.database_summary_message:
        display_screen.write_status(
            "ok" if not plan.database_deployment_needed else "info",
            plan.database_summary_message,
        )
    display_screen.write_blank()
    display_screen.write_summary("Database targets:")
    if plan.database_candidates:
        for entry in plan.database_candidates:
            display_screen.write_summary(
                f"  - {entry['name']}: {entry['target_status']} → {entry['action']}"
            )
        display_screen.write_summary(
            f"  summary: {plan.database_import_count} import, {plan.database_skip_count} skip"
        )
    else:
        display_screen.write_summary("  (none)")
    display_screen.write_blank()
    display_screen.write_summary("Database actions:")
    for command in plan.database_commands or ["  (none — no CREATE + IMPORT planned)"]:
        display_screen.write_summary(f"  - {command}")
    display_screen.write_blank()
    display_screen.write_summary("Repository actions:")
    for command in plan.repository_commands or ["  (none planned)"]:
        display_screen.write_summary(f"  - {command}")
    for warning in plan.warnings:
        display_screen.write_status("warn", warning)
    for blocker in plan.database_blockers:
        display_screen.write_status("fail", f"Databases: {blocker}")
    for blocker in plan.repository_blockers:
        display_screen.write_status("fail", f"Repositories: {blocker}")
