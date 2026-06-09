"""Lightweight deployment snapshot for dashboard and menu status."""

from __future__ import annotations

from dataclasses import dataclass

from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.deploy.models import DeployOptions, DeploymentCandidate
from mercury.deploy.plan import build_deployment_plan


@dataclass(frozen=True)
class DeploymentSnapshot:
    verified_backup_count: int
    protected_source_count: int
    on_server_count: int
    import_count: int
    skip_count: int
    block_count: int
    deployment_needed: bool
    summary_message: str | None
    candidates: tuple[DeploymentCandidate, ...]


def build_deployment_snapshot(
    *,
    policy: ExecutionPolicy | None = None,
    options: DeployOptions | None = None,
    execute: bool = False,
    row_fn=None,
) -> DeploymentSnapshot:
    """Target-aware deploy counts shared by dashboard, menu, and use-case detection."""
    plan = build_deployment_plan(
        policy=policy or load_execution_policy(),
        options=options,
        execute=execute,
        row_fn=row_fn,
    )
    on_server = sum(1 for candidate in plan.candidates if candidate.exists_on_server)
    return DeploymentSnapshot(
        verified_backup_count=len(plan.candidates),
        protected_source_count=len(plan.candidates),
        on_server_count=on_server,
        import_count=plan.import_count,
        skip_count=plan.skip_count,
        block_count=plan.block_count,
        deployment_needed=plan.deployment_needed,
        summary_message=plan.summary_message,
        candidates=tuple(plan.candidates),
    )


def deployment_target_dashboard_label(snapshot: DeploymentSnapshot) -> str:
    """MariaDB target line for the main menu dashboard."""
    total = snapshot.protected_source_count
    if total == 0:
        return "no protected sources configured"
    if not snapshot.deployment_needed and snapshot.on_server_count == total:
        return f"{snapshot.on_server_count} of {total} on server; deploy not needed"
    if snapshot.import_count == 0 and snapshot.skip_count:
        return f"{snapshot.on_server_count} of {total} on server; {snapshot.skip_count} skipped"
    return f"{snapshot.on_server_count} of {total} on server; {snapshot.import_count} to import"
