"""Combined database + repository deployment planning."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.core.execution_policy import REQUIRED_BACKUP_MOUNT
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.repos.build_plan import build_repo_deploy_plan
from mercury.deploy.target_status import target_status_label
from mercury.repo.config import load_repo_bundle_settings
from mercury.transfer.bundle import build_transfer_bundle


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


def _runbook_text(plan: SystemDeployPlan) -> str:
    transfer = build_transfer_bundle(live=True)
    lines = [
        "# Mercury system deployment runbook",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Target host: {plan.hostname}",
        f"Mode: {plan.mode}",
        "",
        "Purpose:",
        "Deploy Mercury-managed databases and artifacts onto a new Fedora system or VM.",
        "",
        "Database deployment summary:",
        f"- deployment_needed: {plan.database_deployment_needed}",
        f"- import_count: {plan.database_import_count}",
        f"- skip_count: {plan.database_skip_count}",
    ]
    if plan.database_summary_message:
        lines.append(f"- summary: {plan.database_summary_message}")
    lines.extend(
        [
            "",
            "Database candidates:",
        ]
    )
    if plan.database_candidates:
        for entry in plan.database_candidates:
            lines.append(
                f"- {entry['name']}: {entry['target_status']} -> {entry['action']}"
            )
    else:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "Planned database commands:",
        ]
    )
    for command in plan.database_commands or ["- (none)"]:
        lines.append(f"- {command}" if command != "- (none)" else command)
    lines.extend(
        [
            "",
            "Repository deployment inputs:",
            f"- latest transfer manifest: {transfer.latest_transfer_manifest_path or 'missing'}",
            f"- latest transfer runbook: {transfer.latest_transfer_runbook_path or 'missing'}",
            f"- configured repos: {len(transfer.repo_entries)}",
            f"- dirty repos: {len(transfer.dirty_repo_names)}",
            "",
            "Post-deployment verification:",
            "- Verify imported databases exist and table counts are sane.",
            "- Run Mercury restore-checks against latest verified backups before handoff.",
            "- Verify repository bundles/runbooks were applied as expected.",
            "",
            "Safety notes:",
            "- System Deployment is not emergency disaster recovery.",
            "- Existing databases are skipped by default and are never silently overwritten.",
            "- Live imports require explicit confirmation.",
        ]
    )
    for warning in plan.warnings:
        lines.append(f"- warning: {warning}")
    for blocker in plan.database_blockers:
        lines.append(f"- database blocker: {blocker}")
    for blocker in plan.repository_blockers:
        lines.append(f"- repository blocker: {blocker}")
    lines.append("")
    return "\n".join(lines)


def write_system_deploy_runbook(plan: SystemDeployPlan) -> Path:
    settings = load_repo_bundle_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = settings.runbook_dir / f"system_deployment_runbook_{stamp}.md"
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REQUIRED_BACKUP_MOUNT)
    except ValueError as exc:
        raise ValueError(f"path is not under {REQUIRED_BACKUP_MOUNT}: {resolved}") from exc
    if not REQUIRED_BACKUP_MOUNT.is_mount():
        raise ValueError(f"required USB mount is not active: {REQUIRED_BACKUP_MOUNT}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(_runbook_text(plan), encoding="utf-8")
    return resolved
