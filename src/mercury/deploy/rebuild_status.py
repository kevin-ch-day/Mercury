"""Post-rebuild checkpoint — databases, repositories, USB, and next steps."""

from __future__ import annotations

from dataclasses import dataclass, field

from mercury.core.execution_policy import load_execution_policy
from mercury.deploy.snapshot import build_deployment_snapshot
from mercury.repo import inspect_repositories, load_repo_definitions


@dataclass(frozen=True)
class RebuildStatusReport:
    hostname: str
    databases_deployed: int
    databases_total: int
    deployment_needed: bool
    repositories_deployed: int
    repositories_total: int
    repositories_missing: list[str] = field(default_factory=list)
    usb_healthy: bool = True
    mariadb_connected: bool = False
    verified_backups: str = "0 of 0"
    sync_ready: int = 0
    sync_blocked: int = 0
    sync_blocker: str = "None."
    deploy_status: str = "unknown"
    recommended_next: str = "./run.sh menu"
    cleanup_suggestions: list[str] = field(default_factory=list)


def _sync_summary() -> tuple[int, int, str]:
    try:
        from mercury.sync.readiness import build_sync_readiness_report

        report = build_sync_readiness_report(live=True)
        blocker = "None."
        messages: list[str] = []
        for entry in report.entries:
            messages.extend(entry.blockers)
        if messages:
            blocker = messages[0]
        return report.ready_count, report.blocked_count, blocker
    except Exception:
        return 0, 0, "Sync readiness unavailable."


def _is_sync_only_blocker(message: str) -> bool:
    text = message.lower()
    return "dev target missing" in text or "dev target" in text


def detect_leftover_databases(server_names: set[str]) -> list[tuple[str, str]]:
    """Return non-protected server databases with cleanup SQL suggestions."""
    from mercury.backup.batch_runner import resolve_batch_sources
    from mercury.database.core import classify_database

    protected = set(resolve_batch_sources(live=True))
    system_names = {
        "mysql",
        "information_schema",
        "performance_schema",
        "sys",
    }
    suggestions: list[tuple[str, str]] = []
    for name in sorted(server_names):
        if name in protected or name in system_names:
            continue
        classification = classify_database(name)
        if classification.role.value == "development":
            continue
        if not (classification.manual_review or name.endswith("_test") or "_test" in name):
            continue
        suggestions.append(
            (
                name,
                f"mariadb -e \"DROP DATABASE IF EXISTS `{name}`;\"  # non-protected leftover",
            )
        )
    return suggestions


def build_rebuild_status_report(*, probe_database: bool = True) -> RebuildStatusReport:
    from mercury.core.environment_status import build_environment_status

    env = build_environment_status(probe_database=probe_database)
    policy = env.policy
    snapshot = build_deployment_snapshot(execute=False) if probe_database else None

    repos = load_repo_definitions()
    repo_statuses = inspect_repositories(repos) if repos else []
    repo_deployed = sum(1 for status in repo_statuses if status.exists and status.git_repo and not status.error)
    repo_missing = [
        status.display_name
        for status in repo_statuses
        if not (status.exists and status.git_repo and not status.error)
    ]

    db_deployed = snapshot.on_server_count if snapshot else 0
    db_total = snapshot.protected_source_count if snapshot else 0
    deployment_needed = snapshot.deployment_needed if snapshot else True

    if deployment_needed:
        deploy_status = "incomplete"
    elif db_deployed == db_total and db_total > 0:
        deploy_status = "complete"
    else:
        deploy_status = "partial"

    sync_ready, sync_blocked, sync_blocker = _sync_summary() if probe_database else (0, 0, "None.")

    cleanup: list[str] = []
    if probe_database and env.mariadb.connection_works is True:
        from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config

        cfg = try_load_mariadb_config()
        if cfg is not None:
            try:
                cleanup = [cmd for _name, cmd in detect_leftover_databases(set(fetch_user_database_names(cfg)))]
            except Exception:
                cleanup = []

    usb_healthy = not any(check.needs_repair for check in env.permission_checks)

    if deploy_status == "complete" and not deployment_needed:
        if policy.backup_execution_allowed():
            recommended = "./run.sh backup all"
        else:
            recommended = "./run.sh backup plan"
    elif deployment_needed:
        recommended = "./run.sh deploy system --dry-run"
    else:
        recommended = "./run.sh menu"

    import socket

    verified_line = "0 of 0"
    if snapshot:
        verified_line = f"{snapshot.verified_backup_count} of {snapshot.protected_source_count} verified"

    return RebuildStatusReport(
        hostname=socket.gethostname(),
        databases_deployed=db_deployed,
        databases_total=db_total,
        deployment_needed=deployment_needed,
        repositories_deployed=repo_deployed,
        repositories_total=len(repos),
        repositories_missing=repo_missing,
        usb_healthy=usb_healthy,
        mariadb_connected=env.mariadb.connection_works is True,
        verified_backups=verified_line,
        sync_ready=sync_ready,
        sync_blocked=sync_blocked,
        sync_blocker=sync_blocker,
        deploy_status=deploy_status,
        recommended_next=recommended,
        cleanup_suggestions=cleanup,
    )


def sync_blocker_is_rebuild_blocker(sync_blocker: str, *, deploy_complete: bool) -> bool:
    """True when sync message should not appear as the main environment blocker."""
    if sync_blocker in {"None.", ""}:
        return False
    if deploy_complete and _is_sync_only_blocker(sync_blocker):
        return False
    return True
