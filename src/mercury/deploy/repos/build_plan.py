"""Build repository deployment plans."""

from __future__ import annotations

import socket

from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.deploy.repos.models import RepoDeployOptions, RepoDeployPlan
from mercury.deploy.repos.plan import planned_repo_commands
from mercury.deploy.repos.preflight import run_repo_deploy_preflight
from mercury.deploy.repos.selection import resolve_repo_deploy_candidates


def build_repo_deploy_plan(
    *,
    policy: ExecutionPolicy | None = None,
    selected_keys: list[str] | None = None,
    options: RepoDeployOptions | None = None,
    source_mode: str = "auto",
    execute: bool = False,
) -> RepoDeployPlan:
    resolved = policy or load_execution_policy()
    opts = options or RepoDeployOptions()
    preflight = run_repo_deploy_preflight(source_mode=source_mode)
    candidates = resolve_repo_deploy_candidates(
        selected_keys=selected_keys,
        prefer_usb_bundle=opts.prefer_usb_bundle,
        source_mode=source_mode,
    )

    blockers = list(preflight.planning_blockers if not execute else preflight.blockers)
    warnings = list(preflight.warnings)
    if not execute:
        for detail in preflight.live_blockers:
            warnings.append(f"Live deploy blocked: {detail}")
    safety_notes = [
        "Repository deployment clones into configured paths from config/repos.toml.",
        "Existing git repositories are skipped by default; nothing is deleted or overwritten.",
        "Operator-storage git bundles capture committed history only (no dirty/untracked files).",
    ]

    if execute and not resolved.live_execution_allowed():
        blockers.append(resolved.refusal_reason() or "Live repository deployment is not permitted.")

    planned_commands: list[str] = []
    for candidate in candidates:
        if candidate.exists_on_system and opts.skip_existing:
            candidate.skip_reason = candidate.skip_reason or f"Repository exists at {candidate.target_path}"
            if execute:
                warnings.append(f"{candidate.display_name}: {candidate.skip_reason}")
            continue
        if candidate.source == "none":
            if execute:
                warnings.append(f"{candidate.display_name}: no deployment source")
            continue
        commands, skip_reason = planned_repo_commands(candidate, options=opts)
        if skip_reason:
            candidate.skip_reason = skip_reason
            if execute:
                warnings.append(f"{candidate.display_name}: {skip_reason}")
            continue
        planned_commands.extend(commands)

    mode = "live" if execute and resolved.live_execution_allowed() else "dry-run"
    return RepoDeployPlan(
        mode=mode,
        hostname=socket.gethostname(),
        source_mode=source_mode,
        execute=execute,
        candidates=candidates,
        planned_commands=planned_commands,
        blockers=blockers,
        warnings=warnings,
        safety_notes=safety_notes,
    )
