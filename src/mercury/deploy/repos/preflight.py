"""Repository deployment preflight checks."""

from __future__ import annotations

import shutil
import socket

from mercury.core.environment_status import build_environment_status
from mercury.core.paths import REPOS_LOCAL
from mercury.deploy.models import DeploymentPreflight, PreflightCheck
from mercury.deploy.repos.selection import resolve_repo_deploy_candidates


def run_repo_deploy_preflight(*, source_mode: str = "auto") -> DeploymentPreflight:
    env = build_environment_status(probe_database=False)
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

    if not REPOS_LOCAL.exists():
        checks.append(
            PreflightCheck(
                label="Repository config",
                level="blocked",
                detail="config/repos.toml missing — run ./run.sh repo init-config",
            )
        )
    else:
        checks.append(PreflightCheck(label="Repository config", level="ready", detail="repos.toml present"))

    if shutil.which("git") is None:
        checks.append(
            PreflightCheck(label="Git client", level="blocked", detail="git command not found in PATH")
        )
    else:
        checks.append(PreflightCheck(label="Git client", level="ready", detail="found"))

    candidates = resolve_repo_deploy_candidates(source_mode=source_mode)
    deployable = [c for c in candidates if not c.exists_on_system and c.source != "none"]
    missing_sources = [c for c in candidates if c.source == "none" and not c.exists_on_system]
    if not candidates:
        checks.append(
            PreflightCheck(
                label="Configured repositories",
                level="blocked",
                detail="No repositories configured in config/repos.toml",
            )
        )
    elif not any(c.source != "none" for c in candidates if not c.exists_on_system):
        checks.append(
            PreflightCheck(
                label="Deployment sources",
                level="blocked",
                detail="No GitHub remote_url or operator-storage bundle available for missing repositories",
            )
        )
    else:
        ready_count = sum(1 for c in candidates if c.source != "none" and not c.exists_on_system)
        checks.append(
            PreflightCheck(
                label="Deployment sources",
                level="ready",
                detail=f"{ready_count} repository source(s) ready to deploy",
            )
        )

    if missing_sources:
        names = ", ".join(c.display_name for c in missing_sources)
        checks.append(
            PreflightCheck(
                label="Missing sources",
                level="warning",
                detail=f"No source for: {names} (add remote_url or operator-storage bundle)",
            )
        )

    from mercury.repo import load_repo_definitions
    from mercury.repo.path_repair import stale_repo_path_detail

    stale = [
        detail
        for repo in load_repo_definitions()
        if (detail := stale_repo_path_detail(repo.path)) is not None
    ]
    if stale:
        checks.append(
            PreflightCheck(
                label="Stale repository paths",
                level="warning",
                detail="; ".join(stale[:3]) + (" …" if len(stale) > 3 else "")
                + " — run ./run.sh repo init-config --force to persist fixes",
            )
        )

    ready = not any(c.level == "blocked" for c in checks)
    return DeploymentPreflight(
        hostname=socket.gethostname(),
        checks=checks,
        ready=ready,
    )
