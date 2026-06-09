"""Compact status rows for deploy menu sub-screens."""

from __future__ import annotations

from mercury.core.execution_policy import load_execution_policy
from mercury.deploy.repos.build_plan import build_repo_deploy_plan
from mercury.deploy.snapshot import build_deployment_snapshot, deployment_target_dashboard_label
from mercury.terminal.theme import dashboard_row


def database_deploy_status_rows() -> list[str]:
    """Operator snapshot for the Deploy Databases submenu."""
    policy = load_execution_policy()
    snapshot = build_deployment_snapshot(policy=policy, execute=False)
    rows = [
        dashboard_row(
            "Verified backups",
            f"{snapshot.verified_backup_count} of {snapshot.protected_source_count} ready",
        ),
        dashboard_row("MariaDB targets", deployment_target_dashboard_label(snapshot)),
        dashboard_row(
            "Execution mode",
            "LIVE" if policy.live_execution_allowed() else "DRY RUN",
        ),
    ]
    if not snapshot.deployment_needed and snapshot.on_server_count:
        rows.append(dashboard_row("Next step", "./run.sh db inventory"))
    elif not policy.live_execution_allowed():
        rows.append(dashboard_row("Next step", "Option 2 — dry-run deploy plan"))
    elif snapshot.import_count:
        rows.append(
            dashboard_row(
                "Live deploy",
                f"would import {snapshot.import_count}, skip {snapshot.skip_count}",
            )
        )
    return rows


def repository_deploy_status_rows() -> list[str]:
    """Operator snapshot for the Deploy Repositories submenu."""
    from mercury.deploy.repos.selection import resolve_repo_deploy_candidates

    policy = load_execution_policy()
    candidates = resolve_repo_deploy_candidates(source_mode="auto")
    missing = [c for c in candidates if not c.exists_on_system and c.source != "none"]
    blocked = [c for c in candidates if not c.exists_on_system and c.source == "none"]
    rows = [
        dashboard_row("Configured repos", str(len(candidates))),
        dashboard_row("Ready to deploy", f"{len(missing)} missing with source"),
        dashboard_row(
            "Execution mode",
            "LIVE" if policy.live_execution_allowed() else "DRY RUN",
        ),
    ]
    if blocked:
        rows.append(dashboard_row("No source", f"{len(blocked)} repo(s) need remote_url or USB bundle"))
    if not policy.live_execution_allowed():
        rows.append(dashboard_row("Next step", "Option 2 — dry-run repository plan"))
    elif missing:
        rows.append(dashboard_row("Next step", "Option 2, then option 3 or 4 for live deploy"))
    else:
        rows.append(dashboard_row("Status", "All configured repositories present"))
    return rows


def deploy_hub_status_rows() -> list[str]:
    """Operator snapshot for the Deploy to This System hub."""
    policy = load_execution_policy()
    db_snapshot = build_deployment_snapshot(policy=policy, execute=False)
    repo_plan = build_repo_deploy_plan(execute=False, source_mode="auto")
    repo_count = sum(
        1
        for c in repo_plan.candidates
        if not c.exists_on_system and c.source != "none"
    )
    rows = [
        dashboard_row("Databases to import", str(db_snapshot.import_count)),
        dashboard_row("Repositories to deploy", str(repo_count)),
        dashboard_row(
            "Execution mode",
            "LIVE" if policy.live_execution_allowed() else "DRY RUN",
        ),
    ]
    if not db_snapshot.deployment_needed and repo_count == 0:
        rows.append(dashboard_row("Status", "System deployment not needed"))
        rows.append(dashboard_row("Next step", "./run.sh db inventory"))
    else:
        rows.append(dashboard_row("Next step", "Option 3 — full system dry-run plan"))
    return rows
