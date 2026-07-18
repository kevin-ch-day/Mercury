"""Execute database deployment from verified operator-storage backups."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from mercury.backup.backup_runner import BackupExecutionError
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.client import run_client_sql, select_client_tool
from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.session import try_load_mariadb_config
from mercury.deploy.models import (
    DeployOptions,
    DeploymentBatchResult,
    DeploymentCandidate,
    DeploymentExecutionResult,
)
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.actions import resolve_deploy_action
from mercury.deploy.safety import assert_deployment_target
from mercury.deploy.verification import verify_deployed_database
from mercury.restore.restore_runner import ImportRunner, build_import_argv

SqlRunner = Callable[[MariaDbConnectionConfig, str], None]


def _client_env(config: MariaDbConnectionConfig) -> dict[str, str]:
    env = os.environ.copy()
    if config.password:
        env["MYSQL_PWD"] = config.password
    return env


def _execute_client_sql(config: MariaDbConnectionConfig, sql: str) -> None:
    try:
        run_client_sql(config, sql)
    except MariaDbLiveError as exc:
        raise BackupExecutionError(str(exc)) from exc


def resolve_deployment_report_dir(policy: ExecutionPolicy) -> Path:
    root = policy.backup_root.resolve()
    if root.name == "mercury_backups":
        return root.parent / "mercury_restore_checks" / "deployments"
    return root / "deployments"


def _write_deployment_report(batch: DeploymentBatchResult, policy: ExecutionPolicy) -> Path | None:
    if batch.mode == "dry-run":
        return None
    report_dir = resolve_deployment_report_dir(policy)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = report_dir / day
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = target / f"deployment_{stamp}.json"
    report_path.write_text(
        json.dumps(batch.model_dump(mode="json"), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    batch.report_path = str(report_path)
    return report_path


def execute_deployment_for_candidate(
    *,
    candidate: DeploymentCandidate,
    execute: bool,
    policy: ExecutionPolicy,
    options: DeployOptions | None = None,
    config: MariaDbConnectionConfig | None = None,
    import_runner: ImportRunner | None = None,
    sql_runner: SqlRunner | None = None,
    inspect_row_fn=None,
) -> DeploymentExecutionResult:
    assert_deployment_target(candidate.target_database)
    opts = options or DeployOptions()
    dump_path = Path(candidate.dump_path)
    action_plan = resolve_deploy_action(
        target_database=candidate.target_database,
        dump_path=str(dump_path),
        target_status=candidate.target_status,  # type: ignore[arg-type]
        options=opts,
    )
    commands = action_plan.commands

    if action_plan.action == "SKIP":
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            dry_run=not execute,
            skipped=True,
            message=action_plan.reason or candidate.skip_reason or "skipped",
            commands=commands,
        )

    if action_plan.action == "BLOCKED":
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            dry_run=not execute,
            refused=True,
            message=action_plan.reason or "deployment blocked",
            commands=commands,
        )

    if not execute:
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            dry_run=True,
            message=f"Would deploy {candidate.source_database} into {candidate.target_database}.",
            commands=commands,
        )

    if not policy.live_execution_allowed():
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            refused=True,
            message=policy.refusal_reason() or "Live deployment is not permitted.",
            commands=commands,
        )

    backup_dir = Path(candidate.backup_directory)
    verification = verify_backup_artifacts(
        backup_dir,
        database=candidate.source_database,
        backup_kind=BACKUP_KIND_FULL,
    )
    if not verification.verified:
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            refused=True,
            message="Checksum or backup verification failed; import blocked.",
            commands=commands,
        )
    if not verification.checksum_matches:
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            refused=True,
            message="Checksum mismatch; import blocked.",
            commands=commands,
        )

    cfg = config or try_load_mariadb_config()
    if cfg is None:
        cfg = load_mariadb_config()
    sql = sql_runner or _execute_client_sql
    import_argv = build_import_argv(cfg, candidate.target_database)

    def _rewrite_import_runner(
        argv: list[str],
        env: dict[str, str],
        dump_path: Path,
        config: MariaDbConnectionConfig,
        target: str,
    ) -> None:
        from mercury.database.mariadb.import_stream import run_compressed_sql_import

        run_compressed_sql_import(
            argv,
            env,
            dump_path,
            strip_definer=True,
            rewrite_database=(candidate.source_database, target),
        )

    runner = import_runner or _rewrite_import_runner

    try:
        for command in commands:
            if command.startswith("DROP DATABASE"):
                sql(cfg, command)
            elif command.startswith("CREATE DATABASE"):
                sql(cfg, command)
        runner(import_argv, _client_env(cfg), dump_path, cfg, candidate.target_database)
    except BackupExecutionError as exc:
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            refused=True,
            message=str(exc),
            commands=commands,
        )

    post = verify_deployed_database(
        candidate.target_database,
        manifest_path=Path(candidate.manifest_path),
        config=cfg,
        row_fn=inspect_row_fn,
    )
    return DeploymentExecutionResult(
        source_database=candidate.source_database,
        target_database=candidate.target_database,
        dry_run=False,
        executed=True,
        message=f"Deployed {candidate.source_database} into {candidate.target_database}.",
        commands=commands,
        verification=post,
    )


def execute_deployment_batch(
    *,
    policy: ExecutionPolicy | None = None,
    databases: list[str] | None = None,
    options: DeployOptions | None = None,
    execute: bool = False,
    import_runner: ImportRunner | None = None,
    sql_runner: SqlRunner | None = None,
    inspect_row_fn=None,
) -> DeploymentBatchResult:
    resolved = policy or load_execution_policy()
    opts = options or DeployOptions()
    plan = build_deployment_plan(
        policy=resolved,
        databases=databases,
        options=opts,
        execute=execute,
    )
    batch = DeploymentBatchResult(
        mode=plan.mode,
        hostname=plan.hostname,
    )
    if execute and plan.blockers:
        for candidate in plan.candidates:
            batch.results.append(
                DeploymentExecutionResult(
                    source_database=candidate.source_database,
                    target_database=candidate.target_database,
                    refused=True,
                    message="; ".join(plan.blockers),
                )
            )
        return batch

    for candidate in plan.candidates:
        if candidate.deploy_action == "SKIP":
            batch.results.append(
                DeploymentExecutionResult(
                    source_database=candidate.source_database,
                    target_database=candidate.target_database,
                    skipped=True,
                    message=candidate.action_reason or candidate.skip_reason or "skipped",
                )
            )
            continue
        if candidate.deploy_action == "BLOCKED":
            batch.results.append(
                DeploymentExecutionResult(
                    source_database=candidate.source_database,
                    target_database=candidate.target_database,
                    refused=True,
                    message=candidate.action_reason or candidate.skip_reason or "blocked",
                )
            )
            continue
        batch.results.append(
            execute_deployment_for_candidate(
                candidate=candidate,
                execute=execute,
                policy=resolved,
                options=opts,
                import_runner=import_runner,
                sql_runner=sql_runner,
                inspect_row_fn=inspect_row_fn,
            )
        )

    if execute and batch.deployed_count:
        _write_deployment_report(batch, resolved)
    return batch


def build_import_shell_preview(target_database: str, dump_path: str) -> str:
    """Return the shell import fragment used in plans and tests."""
    tool = select_client_tool()
    if dump_path.endswith(".gz"):
        return f"gunzip -c {dump_path} | {tool} {target_database}"
    return f"{tool} {target_database} < {dump_path}"
