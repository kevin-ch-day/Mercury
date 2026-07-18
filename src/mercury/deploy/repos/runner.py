"""Execute repository deployment plans."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.deploy.repos.build_plan import build_repo_deploy_plan
from mercury.deploy.repos.models import RepoDeployBatchResult, RepoDeployCandidate, RepoDeployOptions, RepoDeployResult
from mercury.deploy.repos.plan import planned_repo_commands
from mercury.repo.config import load_repo_bundle_settings

CloneRunner = Callable[[list[str]], None]


def _default_clone_runner(argv: list[str]) -> None:
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"command failed: {' '.join(argv)}")


def _argv_from_command(command: str) -> list[str]:
    import shlex

    return shlex.split(command)


def resolve_repo_deploy_report_dir() -> Path:
    settings = load_repo_bundle_settings()
    return settings.manifest_dir.parent / "mercury_restore_checks" / "repo_deployments"


def execute_repo_deploy_for_candidate(
    *,
    candidate: RepoDeployCandidate,
    execute: bool,
    policy: ExecutionPolicy,
    options: RepoDeployOptions,
    clone_runner: CloneRunner | None = None,
) -> RepoDeployResult:
    commands, skip_reason = planned_repo_commands(candidate, options=options)
    if skip_reason:
        return RepoDeployResult(
            key=candidate.key,
            display_name=candidate.display_name,
            target_path=candidate.target_path,
            skipped=True,
            message=skip_reason,
            commands=commands,
        )

    if not execute:
        return RepoDeployResult(
            key=candidate.key,
            display_name=candidate.display_name,
            target_path=candidate.target_path,
            dry_run=True,
            message=f"Would deploy {candidate.display_name} to {candidate.target_path}.",
            commands=commands,
        )

    if not policy.live_execution_allowed():
        return RepoDeployResult(
            key=candidate.key,
            display_name=candidate.display_name,
            target_path=candidate.target_path,
            refused=True,
            message=policy.refusal_reason() or "Live deployment is not permitted.",
            commands=commands,
        )

    if candidate.source == "usb_bundle" and candidate.bundle_path:
        bundle = Path(candidate.bundle_path)
        if not bundle.is_file():
            return RepoDeployResult(
                key=candidate.key,
                display_name=candidate.display_name,
                target_path=candidate.target_path,
                refused=True,
                message=f"Operator-storage bundle not found: {bundle}",
                commands=commands,
            )
        verify = subprocess.run(
            ["git", "bundle", "verify", str(bundle)],
            capture_output=True,
            text=True,
            check=False,
        )
        if verify.returncode != 0:
            detail = (verify.stderr or verify.stdout or "").strip()
            return RepoDeployResult(
                key=candidate.key,
                display_name=candidate.display_name,
                target_path=candidate.target_path,
                refused=True,
                message=detail or "git bundle verify failed",
                commands=commands,
            )

    runner = clone_runner or _default_clone_runner
    try:
        for command in commands:
            if command.startswith("git bundle verify"):
                continue
            runner(_argv_from_command(command))
    except RuntimeError as exc:
        return RepoDeployResult(
            key=candidate.key,
            display_name=candidate.display_name,
            target_path=candidate.target_path,
            refused=True,
            message=str(exc),
            commands=commands,
        )

    from mercury.deploy.repos.post_deploy import finalize_deployed_repository

    post_note = finalize_deployed_repository(candidate)
    message = f"Deployed {candidate.display_name} to {candidate.target_path}."
    if post_note:
        message = f"{message} {post_note}"

    return RepoDeployResult(
        key=candidate.key,
        display_name=candidate.display_name,
        target_path=candidate.target_path,
        dry_run=False,
        executed=True,
        message=message,
        commands=commands,
    )


def execute_repo_deploy_batch(
    *,
    policy: ExecutionPolicy | None = None,
    selected_keys: list[str] | None = None,
    options: RepoDeployOptions | None = None,
    source_mode: str = "auto",
    execute: bool = False,
    clone_runner: CloneRunner | None = None,
) -> RepoDeployBatchResult:
    resolved = policy or load_execution_policy()
    opts = options or RepoDeployOptions()
    plan = build_repo_deploy_plan(
        policy=resolved,
        selected_keys=selected_keys,
        options=opts,
        source_mode=source_mode,
        execute=execute,
    )
    batch = RepoDeployBatchResult(
        mode=plan.mode,
        hostname=plan.hostname,
        source_mode=source_mode,
    )
    if execute and plan.blockers:
        for candidate in plan.candidates:
            batch.results.append(
                RepoDeployResult(
                    key=candidate.key,
                    display_name=candidate.display_name,
                    target_path=candidate.target_path,
                    refused=True,
                    message="; ".join(plan.blockers),
                )
            )
        return batch

    for candidate in plan.candidates:
        if candidate.skip_reason and execute and opts.skip_existing and candidate.exists_on_system:
            batch.results.append(
                RepoDeployResult(
                    key=candidate.key,
                    display_name=candidate.display_name,
                    target_path=candidate.target_path,
                    skipped=True,
                    message=candidate.skip_reason,
                )
            )
            continue
        if candidate.source == "none":
            continue
        batch.results.append(
            execute_repo_deploy_for_candidate(
                candidate=candidate,
                execute=execute,
                policy=resolved,
                options=opts,
                clone_runner=clone_runner,
            )
        )

    if execute and batch.deployed_count:
        report_dir = resolve_repo_deploy_report_dir()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = report_dir / day
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_path = target / f"repo_deployment_{stamp}.json"
        report_path.write_text(
            json.dumps(batch.model_dump(mode="json"), indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        batch.report_path = str(report_path)
    return batch
