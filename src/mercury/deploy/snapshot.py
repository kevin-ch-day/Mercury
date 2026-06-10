"""Lightweight deployment snapshot for dashboard and menu status."""

from __future__ import annotations

from dataclasses import dataclass

from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES
from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config
from mercury.deploy.models import DeployOptions, DeploymentCandidate
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.preflight import run_deployment_preflight


@dataclass(frozen=True)
class DeploymentSnapshot:
    verified_backup_count: int
    protected_source_count: int
    on_server_count: int
    missing_source_count: int
    import_count: int
    skip_count: int
    block_count: int
    deployment_needed: bool
    summary_message: str | None
    candidates: tuple[DeploymentCandidate, ...]


def _resolve_server_databases(preflight) -> set[str]:
    if preflight.existing_databases:
        return set(preflight.existing_databases)
    config = try_load_mariadb_config()
    if config is None:
        return set()
    try:
        return set(fetch_user_database_names(config))
    except Exception:
        return set()


def build_deployment_snapshot(
    *,
    policy: ExecutionPolicy | None = None,
    options: DeployOptions | None = None,
    execute: bool = False,
    row_fn=None,
) -> DeploymentSnapshot:
    """Target-aware deploy counts shared by dashboard, menu, and use-case detection."""
    resolved = policy or load_execution_policy()
    plan = build_deployment_plan(
        policy=resolved,
        options=options,
        execute=execute,
        row_fn=row_fn,
    )
    preflight = run_deployment_preflight(policy=resolved, probe_database=True)
    server_databases = _resolve_server_databases(preflight)
    protected_total = len(ACTIVE_BACKUP_SOURCE_DATABASES)
    on_server = sum(1 for name in ACTIVE_BACKUP_SOURCE_DATABASES if name in server_databases)
    missing = protected_total - on_server
    return DeploymentSnapshot(
        verified_backup_count=len(plan.candidates),
        protected_source_count=protected_total,
        on_server_count=on_server,
        missing_source_count=missing,
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
    base = f"{snapshot.on_server_count} of {total} protected sources on server"
    if snapshot.missing_source_count:
        missing_label = (
            "1 missing"
            if snapshot.missing_source_count == 1
            else f"{snapshot.missing_source_count} missing"
        )
        return f"{base}; {missing_label}"
    if not snapshot.deployment_needed:
        return f"{base}; deploy not needed"
    if snapshot.import_count:
        return f"{base}; {snapshot.import_count} to import"
    if snapshot.skip_count:
        return f"{base}; {snapshot.skip_count} skipped"
    return base
