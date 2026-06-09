"""Build a dry-run or live deployment plan."""

from __future__ import annotations

import socket
from pathlib import Path

from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config
from mercury.deploy.actions import resolve_deploy_action
from mercury.deploy.models import DeployOptions, DeploymentPlan
from mercury.deploy.preflight import run_deployment_preflight
from mercury.deploy.selection import resolve_deployment_candidates
from mercury.deploy.target_status import classify_target_database


def _resolve_server_databases(cfg, preflight) -> set[str]:
    if preflight.existing_databases:
        return set(preflight.existing_databases)
    if cfg is None:
        return set()
    try:
        return set(fetch_user_database_names(cfg))
    except Exception:
        return set()


def _policy_labels(options: DeployOptions) -> tuple[str, bool, bool]:
    policy = "skip-existing" if options.skip_existing else "import-into-existing"
    return policy, options.allow_overwrite_database, options.allow_drop_database


def _build_safety_notes(candidates, *, has_existing_targets: bool) -> list[str]:
    notes = [
        "Deployment imports verified USB backups into local MariaDB.",
        "Never drops target databases unless explicit overwrite/drop modes are enabled.",
    ]
    if has_existing_targets:
        notes.append(
            "Existing target databases detected; default deploy policy will not overwrite them."
        )
    else:
        notes.append("Source databases missing on the server do not block deployment planning.")
    return notes


def _blocked_existing_guidance() -> list[str]:
    return [
        "Deployment blocked by existing target databases.",
        "Default policy will not import into existing protected DBs.",
        "Options: skip existing (default), deploy with suffix, restore-check lane, "
        "or explicit overwrite mode when enabled by policy.",
    ]


def build_deployment_plan(
    *,
    policy: ExecutionPolicy | None = None,
    databases: list[str] | None = None,
    options: DeployOptions | None = None,
    execute: bool = False,
    row_fn=None,
) -> DeploymentPlan:
    resolved = policy or load_execution_policy()
    opts = options or DeployOptions()
    preflight = run_deployment_preflight(policy=resolved, probe_database=True)
    cfg = try_load_mariadb_config()
    server_databases = _resolve_server_databases(cfg, preflight)

    candidates = resolve_deployment_candidates(
        policy=resolved,
        databases=databases,
        existing_on_server=server_databases,
    )

    blockers = list(preflight.planning_blockers if not execute else preflight.blockers)
    warnings = list(preflight.warnings)
    if not execute:
        for detail in preflight.live_blockers:
            warnings.append(f"Live deploy blocked: {detail}")

    has_existing_targets = False
    planned_commands: list[str] = []
    import_count = 0
    skip_count = 0
    block_count = 0
    blocked_existing = False

    for candidate in candidates:
        target_state = classify_target_database(
            candidate.target_database,
            config=cfg,
            server_databases=server_databases,
            manifest_path=Path(candidate.manifest_path),
            row_fn=row_fn,
        )
        candidate.target_status = target_state.status
        candidate.target_status_detail = target_state.detail
        candidate.table_count = target_state.table_count
        candidate.exists_on_server = target_state.exists_on_server
        if target_state.exists_on_server:
            has_existing_targets = True

        action_plan = resolve_deploy_action(
            target_database=candidate.target_database,
            dump_path=candidate.dump_path,
            target_status=target_state.status,
            options=opts,
        )
        candidate.deploy_action = action_plan.action
        candidate.action_reason = action_plan.reason

        if action_plan.action == "SKIP":
            skip_count += 1
            candidate.skip_reason = action_plan.reason
            continue

        if action_plan.action == "BLOCKED":
            block_count += 1
            candidate.skip_reason = action_plan.reason
            if target_state.exists_on_server:
                blocked_existing = True
            message = (
                f"{candidate.target_database}: {action_plan.reason}"
                if action_plan.reason
                else f"{candidate.target_database}: blocked"
            )
            if execute:
                blockers.append(message)
            else:
                warnings.append(message)
            continue

        import_count += 1
        planned_commands.extend(action_plan.commands)

    policy_label, overwrite_enabled, drop_enabled = _policy_labels(opts)
    safety_notes = _build_safety_notes(candidates, has_existing_targets=has_existing_targets)

    all_exist_verified = bool(candidates) and all(
        c.target_status == "exists_verified" and c.deploy_action == "SKIP" for c in candidates
    )
    deployment_needed = import_count > 0

    summary_message: str | None = None
    if all_exist_verified:
        deployment_needed = False
        summary_message = (
            "Deployment not needed. All selected databases already exist on this system. "
            "Recommended next step: ./run.sh db inventory"
        )
    elif import_count == 0 and skip_count == len(candidates) and candidates:
        deployment_needed = False
        summary_message = (
            "Deployment not needed. All selected databases already exist on this system "
            "(skip-existing policy)."
        )
    elif blocked_existing and import_count == 0 and execute:
        summary_message = "Live deployment blocked by existing target databases."
        warnings.extend(_blocked_existing_guidance())

    if execute and not resolved.live_execution_allowed():
        blockers.append(resolved.refusal_reason() or "Live deployment is not permitted.")

    user = cfg.user if cfg else "USER"
    mode = "live" if execute and resolved.live_execution_allowed() else "dry-run"
    return DeploymentPlan(
        mode=mode,
        hostname=socket.gethostname(),
        mariadb_user=user,
        execute=execute,
        candidates=candidates,
        planned_commands=planned_commands,
        blockers=blockers,
        warnings=warnings,
        safety_notes=safety_notes,
        existing_target_policy=policy_label,
        overwrite_enabled=overwrite_enabled,
        drop_enabled=drop_enabled,
        deployment_needed=deployment_needed,
        summary_message=summary_message,
        import_count=import_count,
        skip_count=skip_count,
        block_count=block_count,
    )
