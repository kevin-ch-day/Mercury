"""Execute database deployment from verified operator-storage backups."""

from __future__ import annotations

import json
import shlex
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from mercury.backup.backup_runner import BackupExecutionError
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.database.mariadb.client import (
    client_process_credentials,
    prepend_client_defaults,
    run_client_sql,
    select_client_tool,
)
from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.session import try_load_mariadb_config
from mercury.deploy.models import (
    DeployOptions,
    DeploymentBatchResult,
    DeploymentCandidate,
    DeploymentExecutionResult,
    DeploymentVerification,
    DeploymentPlan,
)
from mercury.deploy.artifacts import bound_deployment_artifacts
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.actions import resolve_deploy_action
from mercury.deploy.safety import assert_deployment_target
from mercury.deploy.verification import verify_deployed_database
from mercury.restore.restore_runner import ImportRunner, build_import_argv

SqlRunner = Callable[[MariaDbConnectionConfig, str], None]


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
    from mercury.storage.host_maintenance import refuse_if_hdd_writes_disabled

    refuse_if_hdd_writes_disabled("deployment report write")
    report_dir = resolve_deployment_report_dir(policy)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = report_dir / day
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    report_path = target / f"deployment_{stamp}.json"
    batch.report_path = str(report_path)
    from mercury.core.artifact_permissions import write_private_text

    write_private_text(report_path,
        json.dumps(batch.model_dump(mode="json"), indent=2, default=str) + "\n",
    )
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
    allow_development_deploy: bool = False,
) -> DeploymentExecutionResult:
    candidate = candidate.model_copy(deep=True)
    assert_deployment_target(
        candidate.target_database, allow_development_deploy=allow_development_deploy,
    )
    if candidate.source_database != candidate.target_database:
        raise BackupExecutionError("Deployment requires the exact authorized source/target database name.")
    opts = options or DeployOptions()
    dump_path = Path(candidate.dump_path)
    if dump_path.is_symlink():
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            refused=True,
            message=f"Refusing symlink dump path: {dump_path}",
            commands=[],
        )
    dump_path = dump_path.expanduser().resolve()
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

    sql = sql_runner or _execute_client_sql

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

    target_created = False
    binding = {"backup_id": candidate.backup_id, "manifest_sha256": candidate.manifest_sha256 or "",
        "checksum_sha256": candidate.checksum_sha256 or "", "dump_sha256": candidate.dump_sha256 or ""}
    try:
        with bound_deployment_artifacts(candidate, backup_root=policy.backup_root,
                allow_development_backup=allow_development_deploy) as (dump_path, manifest_path):
            from mercury.database.mariadb.import_stream import validate_sql_transform

            validate_sql_transform(dump_path, source=candidate.source_database, target=candidate.target_database)
            cfg = (config or try_load_mariadb_config() or load_mariadb_config()).model_copy(deep=True)
            import_argv = build_import_argv(cfg, candidate.target_database)
            # The only permitted DDL is exclusive creation. A target appearing
            # since planning must cause an error, never a reused import target.
            from mercury.database.mariadb.identifiers import quote_ident

            sql(cfg, f"CREATE DATABASE {quote_ident(candidate.target_database)}")
            target_created = True
            with client_process_credentials(cfg) as (env, extra):
                runner(
                    prepend_client_defaults(import_argv, extra), env, dump_path,
                    cfg, candidate.target_database,
                )
            try:
                post = verify_deployed_database(
                    candidate.target_database, manifest_path=manifest_path,
                    config=cfg, row_fn=inspect_row_fn,
                )
            except Exception as exc:  # Verification availability is required for success.
                post = DeploymentVerification(database=candidate.target_database,
                    issues=[f"Post-import verification unavailable: {type(exc).__name__}: {exc}"])
    except (BackupExecutionError, OSError, ValueError) as exc:
        return DeploymentExecutionResult(
            source_database=candidate.source_database,
            target_database=candidate.target_database,
            dry_run=False,
            refused=True,
            target_created=target_created,
            message=f"Deployment failed: {exc}" + (" Newly created target retained for inspection." if target_created else " No import attempted."),
            commands=commands,
            artifact_binding=binding,
        )

    passed = post.verified and not post.issues
    return DeploymentExecutionResult(
        source_database=candidate.source_database,
        target_database=candidate.target_database,
        dry_run=False,
        executed=True,
        refused=not passed,
        target_created=True,
        message=(f"Deployed and verified {candidate.source_database} into {candidate.target_database}." if passed else
            "Import completed, but required deployment verification failed: " + "; ".join(post.issues or [post.detail]) + ". Target retained for inspection."),
        commands=commands,
        verification=post,
        artifact_binding=binding,
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
    allow_development_deploy: bool = False,
    deployment_plan: DeploymentPlan | None = None,
) -> DeploymentBatchResult:
    resolved = policy or load_execution_policy()
    opts = options or DeployOptions()
    plan = deployment_plan.model_copy(deep=True) if deployment_plan is not None else build_deployment_plan(
        policy=resolved,
        databases=databases,
        options=opts,
        execute=execute,
        allow_development_deploy=allow_development_deploy,
    )
    batch = DeploymentBatchResult(
        mode="live" if execute and resolved.live_execution_allowed() else plan.mode,
        hostname=plan.hostname,
        blockers=plan.blockers if execute else [],
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
                allow_development_deploy=allow_development_deploy,
            )
        )
        if execute and batch.results[-1].refused:
            batch.blockers.append("Deployment stopped after failure; remaining targets were not modified.")
            break

    if execute and batch.results and resolved.live_execution_allowed():
        _write_deployment_report(batch, resolved)
    return batch


def build_import_shell_preview(target_database: str, dump_path: str) -> str:
    """Return the shell import fragment used in plans and tests."""
    tool = select_client_tool()
    quoted_dump = shlex.quote(dump_path)
    quoted_target = shlex.quote(target_database)
    if dump_path.endswith(".gz"):
        return f"gunzip -c {quoted_dump} | {tool} {quoted_target}"
    return f"{tool} {quoted_target} < {quoted_dump}"
