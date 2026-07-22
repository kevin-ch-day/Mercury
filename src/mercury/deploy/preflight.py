"""Deployment preflight checks for a fresh Fedora/MariaDB host."""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

from mercury.core.environment_status import build_environment_status
from mercury.core.execution_policy import (
    ExecutionPolicy,
    backup_root_state_is_ready,
    load_execution_policy,
)
from mercury.core.path_permissions import check_path_permission
from mercury.core.setup_paths import MERCURY_OPERATOR_STORAGE_DIRS
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config
from mercury.deploy.models import DeploymentPreflight, PreflightCheck
from mercury.deploy.privileges import deployment_grants_sufficient
from mercury.deploy.selection import resolve_deployment_candidates
from mercury.deploy.target_status import classify_target_database, target_status_label


def _hostname() -> str:
    return socket.gethostname()


def _check_privileges(config) -> PreflightCheck:
    ok, detail = deployment_grants_sufficient(config)
    if ok:
        return PreflightCheck(label="MariaDB privileges", level="ready", detail=detail)
    return PreflightCheck(
        label="MariaDB privileges",
        level="blocked",
        detail=f"Configured user lacks deployment privileges ({detail})",
        live_only=True,
    )


def _operator_storage_writable_check(policy) -> PreflightCheck:
    """Check configured active storage, not the legacy USB discovery object."""
    blocked_paths: list[str] = []
    for dirname in MERCURY_OPERATOR_STORAGE_DIRS:
        path = policy.operator_mount / dirname
        check = check_path_permission(path, label=dirname)
        if check.needs_repair:
            blocked_paths.append(dirname)
    if not blocked_paths:
        return PreflightCheck(label="Operator storage writability", level="ready", detail="operator-writable")
    names = ", ".join(blocked_paths)
    return PreflightCheck(
        label="Operator storage writability",
        level="blocked",
        detail=f"Operator storage paths not writable by current user: {names}",
        live_only=True,
    )


def _disk_space_check(required_bytes: int) -> PreflightCheck:
    from mercury.core.platform import detect_platform

    candidates: list[Path]
    if detect_platform().is_windows:
        candidates = [
            Path("C:/ProgramData/MariaDB"),
            Path("C:/Program Files/MariaDB"),
            Path("C:/"),
        ]
    else:
        candidates = [Path("/var/lib/mysql"), Path("/")]

    mysql_dir = next((path for path in candidates if path.exists()), candidates[-1])
    try:
        usage = shutil.disk_usage(mysql_dir)
    except OSError as exc:
        return PreflightCheck(
            label="Disk space",
            level="warning",
            detail=f"Could not assess disk space: {exc}",
            live_only=True,
        )
    free = usage.free
    if free < required_bytes:
        return PreflightCheck(
            label="Disk space",
            level="blocked",
            detail=f"Insufficient free space under {mysql_dir}: need {required_bytes} bytes, have {free}",
            live_only=True,
        )
    return PreflightCheck(
        label="Disk space",
        level="ready",
        detail=f"{free // (1024 * 1024)} MiB free",
    )


def run_deployment_preflight(
    *,
    policy: ExecutionPolicy | None = None,
    probe_database: bool = True,
) -> DeploymentPreflight:
    resolved = policy or load_execution_policy()
    env = build_environment_status(probe_database=probe_database)
    checks: list[PreflightCheck] = []

    if not env.config.initialized:
        checks.append(
            PreflightCheck(
                label="Local config",
                level="blocked",
                detail="Local config missing — run ./run.sh config init",
            )
        )
    else:
        checks.append(PreflightCheck(label="Local config", level="ready", detail="initialized"))

    if resolved.config_path is None:
        checks.append(
            PreflightCheck(
                label="Backup root",
                level="blocked",
                detail="Backup root not configured in local.toml",
            )
        )
    elif resolved.backup_root_is_within_repo() and not resolved.allow_unsafe_backup_root:
        checks.append(
            PreflightCheck(
                label="Backup root",
                level="blocked",
                detail="Repo-local backup fallback cannot be used for live deployment",
                live_only=True,
            )
        )
    elif not backup_root_state_is_ready(resolved.backup_root_state()):
        checks.append(
            PreflightCheck(
                label="Operator backup root",
                level="blocked",
                detail=f"Operator backup root not ready ({resolved.backup_root_state()})",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                label="Operator backup root",
                level="ready",
                detail="mounted and configured",
            )
        )
    checks.append(_operator_storage_writable_check(resolved))

    if env.mariadb.service_state == "inactive":
        checks.append(
            PreflightCheck(label="MariaDB service", level="blocked", detail="MariaDB service inactive")
        )
    elif not env.mariadb.mariadb_client_found:
        checks.append(
            PreflightCheck(label="MariaDB client", level="blocked", detail="mariadb/mysql client not found")
        )
    else:
        checks.append(PreflightCheck(label="MariaDB client", level="ready", detail="found"))

    existing: list[str] = []
    cfg = try_load_mariadb_config()
    if env.mariadb.connection_works is False:
        checks.append(
            PreflightCheck(
                label="MariaDB connection",
                level="blocked",
                detail=f"MariaDB auth failed for {env.mariadb.configured_user or 'configured user'}",
            )
        )
    elif env.mariadb.connection_works is True and cfg is not None:
        checks.append(PreflightCheck(label="MariaDB connection", level="ready", detail="connected"))
        checks.append(_check_privileges(cfg))
        try:
            existing = fetch_user_database_names(cfg)
        except (MariaDbLiveError, OSError):
            checks.append(
                PreflightCheck(
                    label="Server inventory",
                    level="warning",
                    detail="Could not list existing databases (planning continues)",
                )
            )
    elif probe_database:
        checks.append(
            PreflightCheck(
                label="MariaDB connection",
                level="blocked",
                detail="MariaDB connection not available",
            )
        )

    candidates = resolve_deployment_candidates(
        policy=resolved,
        existing_on_server=set(existing),
    )
    if not candidates:
        checks.append(
            PreflightCheck(
                label="Verified backups",
                level="blocked",
                detail="No verified full backups found for protected sources on operator storage",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                label="Verified backups",
                level="ready",
                detail=f"{len(candidates)} verified backup(s) resolved",
            )
        )
        required = sum(max(c.size_bytes, 1) for c in candidates)
        checks.append(_disk_space_check(required))

    if candidates and env.mariadb.connection_works is True:
        server_set = set(existing)
        overlap_details: list[str] = []
        importable = 0
        for candidate in candidates:
            state = classify_target_database(
                candidate.target_database,
                config=cfg,
                server_databases=server_set,
                manifest_path=Path(candidate.manifest_path),
            )
            candidate.exists_on_server = state.exists_on_server
            if state.status == "missing":
                importable += 1
                continue
            overlap_details.append(
                f"{candidate.target_database} ({target_status_label(state.status)})"
            )
        if overlap_details and importable:
            checks.append(
                PreflightCheck(
                    label="Target databases",
                    level="warning",
                    detail=(
                        f"{len(overlap_details)} on server, {importable} missing/importable — "
                        + "; ".join(overlap_details[:3])
                        + (" …" if len(overlap_details) > 3 else "")
                        + "; live deploy skips existing by default"
                    ),
                )
            )
        elif overlap_details:
            checks.append(
                PreflightCheck(
                    label="Target databases",
                    level="ready",
                    detail=(
                        f"{len(overlap_details)} on server — "
                        + "; ".join(overlap_details[:3])
                        + (" …" if len(overlap_details) > 3 else "")
                        + "; deploy not needed (skip-existing)"
                    ),
                )
            )
        elif importable:
            checks.append(
                PreflightCheck(
                    label="Target databases",
                    level="ready",
                    detail=f"{importable} protected database(s) missing on server and importable",
                )
            )

    ready = not any(c.level == "blocked" for c in checks)
    return DeploymentPreflight(
        hostname=_hostname(),
        checks=checks,
        existing_databases=sorted(existing),
        ready=ready,
    )
