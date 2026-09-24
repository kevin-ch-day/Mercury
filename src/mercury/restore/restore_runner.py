"""Restore verified logical backups into dev or restore-check targets."""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, Field

from mercury.backup.backup_runner import BackupExecutionError, assert_not_production_restore_target
from mercury.database.core import DatabaseRole, classify_database
from mercury.database.mariadb.client import (
    client_process_credentials,
    prepend_client_defaults,
    run_client_query,
    run_client_sql,
    select_client_tool,
)
from mercury.database.mariadb.config import (
    MariaDbConfigError,
    MariaDbConnectionConfig,
    assert_connection_tls,
    load_mariadb_config,
    load_mariadb_restore_config,
)
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.identifiers import assert_safe_identifier, quote_ident
from mercury.database.mariadb.session import try_load_mariadb_config
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.backup.checksum import sha256_file

ImportRunner = Callable[
    [list[str], dict[str, str], Path, MariaDbConnectionConfig, str],
    None,
]


class RestoreExecutionResult(BaseModel):
    source_database: str
    target_database: str
    dump_path: str
    dry_run: bool = True
    executed: bool = False
    refused: bool = False
    message: str = ""
    commands: list[str] = Field(default_factory=list)
    cleanup_dropped: bool = False
    cleanup_command: str | None = None
    verification_passed: bool | None = None
    verification_detail: str | None = None
    verification_issues: list[str] = Field(default_factory=list)
    target_table_count: int | None = None
    receipt_path: str | None = None


def _quote_restore_ident(name: str, *, what: str) -> str:
    try:
        return quote_ident(name, what=what)
    except ValueError as exc:
        raise BackupExecutionError(str(exc)) from exc


def assert_safe_restore_target(database: str) -> None:
    """Only disposable dev targets and _restorecheck_* temp databases."""
    _quote_restore_ident(database, what="restore target")
    assert_not_production_restore_target(database, operation="restore")
    role = classify_database(database).role
    if role in {DatabaseRole.DEVELOPMENT, DatabaseRole.RESTORE_CHECK_TEMP}:
        return
    raise BackupExecutionError(
        f"Refusing restore into '{database}': only *_dev and _restorecheck_* targets are allowed."
    )


def assert_governed_production_cutover_target(database: str) -> None:
    """Allow only the two fixed catalog names in the sealed cutover lane.

    This intentionally is not a general production-restore escape hatch.  The
    production-cutover service additionally binds these names to a verified
    package, rehearsal evidence, a one-time preview receipt, and a rollback
    contract before it can call this executor.
    """
    _quote_restore_ident(database, what="production cutover target")
    if database not in {"android_permission_intel", "erebus_threat_intel_prod"}:
        raise BackupExecutionError(
            f"Refusing governed production restore into unexpected target '{database}'."
        )


def assert_governed_destination_recovery_target(database: str) -> None:
    """Allow only the five explicitly approved missing destination schemas."""
    _quote_restore_ident(database, what="destination recovery target")
    allowed = {
        "android_permission_intel_dev",
        "erebus_threat_intel_dev",
        "scytaledroid_core_prod",
        "scytaledroid_core_dev",
        "obsidiandroid_core_prod",
    }
    if database not in allowed:
        raise BackupExecutionError(
            f"Refusing governed destination recovery into unexpected target '{database}'."
        )


def build_import_argv(config: MariaDbConnectionConfig, database: str) -> list[str]:
    try:
        assert_safe_identifier(database, what="import target")
        assert_connection_tls(config)
    except (ValueError, MariaDbConfigError) as exc:
        raise BackupExecutionError(str(exc)) from exc
    tool = select_client_tool()
    argv = [tool, "-u", config.user, "--max-allowed-packet=1G", database]
    if config.unix_socket:
        argv[1:1] = [f"--socket={config.unix_socket}", "--protocol=SOCKET"]
    else:
        argv[1:1] = ["-h", config.host, "-P", str(config.port)]
        if config.ssl_disabled:
            argv[1:1] = ["--skip-ssl"]
    return argv


def _assert_regular_dump_file(path: Path) -> None:
    if path.is_symlink():
        raise BackupExecutionError(f"Refusing symlink dump path: {path}")
    if not path.is_file():
        raise BackupExecutionError(f"Dump file not found: {path}")


def _execute_client_sql(config: MariaDbConnectionConfig, sql: str) -> None:
    try:
        run_client_sql(config, sql)
    except MariaDbLiveError as exc:
        raise BackupExecutionError(str(exc)) from exc


def _default_import_runner(
    argv: list[str],
    env: dict[str, str],
    dump_path: Path,
    _config: MariaDbConnectionConfig,
    target: str,
    *,
    source_database: str,
    schema_rewrites: Mapping[str, str] | None = None,
    on_progress=None,
) -> None:
    from mercury.database.mariadb.import_stream import run_compressed_sql_import

    run_compressed_sql_import(
        argv,
        env,
        dump_path,
        strip_definer=True,
        rewrite_database=(source_database, target),
        rewrite_databases=schema_rewrites,
        on_progress=on_progress,
    )


def _make_import_runner(
    source_database: str,
    target_database: str,
    *,
    schema_rewrites: Mapping[str, str] | None = None,
    on_progress=None,
) -> ImportRunner:
    def runner(
        argv: list[str],
        env: dict[str, str],
        dump_path: Path,
        config: MariaDbConnectionConfig,
        target: str,
    ) -> None:
        _default_import_runner(
            argv,
            env,
            dump_path,
            config,
            target,
            source_database=source_database,
            schema_rewrites=schema_rewrites,
            on_progress=on_progress,
        )

    return runner


def _verify_restore_target(
    target_database: str,
    *,
    manifest_path: Path,
    config: MariaDbConnectionConfig,
    row_fn=None,
):
    from mercury.deploy.verification import verify_deployed_database

    return verify_deployed_database(
        target_database,
        manifest_path=manifest_path,
        config=config,
        row_fn=row_fn,
    )


def execute_restore_into_database(
    *,
    target_database: str,
    dump_path: Path,
    source_database: str,
    execute: bool = False,
    policy: ExecutionPolicy | None = None,
    recreate_target: bool = True,
    cleanup_after_success: bool = False,
    receipt_root: Path | None = None,
    governed_destination_rehearsal: bool = False,
    governed_production_cutover: bool = False,
    governed_destination_recovery: bool = False,
    rollback_new_target_on_failure: bool = False,
    schema_rewrites: Mapping[str, str] | None = None,
    config: MariaDbConnectionConfig | None = None,
    import_runner: ImportRunner | None = None,
    inspect_row_fn=None,
    on_target_created: Callable[[str], None] | None = None,
    on_import_progress=None,
    restore_preflight=None,
    require_restore_preflight: bool = False,
) -> RestoreExecutionResult:
    """Plan or run ``gunzip -c dump | mariadb target`` for verified backups."""
    if governed_production_cutover:
        assert_governed_production_cutover_target(target_database)
    elif governed_destination_recovery:
        assert_governed_destination_recovery_target(target_database)
    else:
        assert_safe_restore_target(target_database)
    quoted_target = _quote_restore_ident(target_database, what="restore target")
    _quote_restore_ident(source_database, what="restore source")
    if schema_rewrites:
        for source, target in schema_rewrites.items():
            _quote_restore_ident(source, what="schema rewrite source")
            _quote_restore_ident(target, what="schema rewrite target")
    resolved = policy or load_execution_policy()
    given_dump = dump_path.expanduser()
    if given_dump.is_symlink():
        raise BackupExecutionError(f"Refusing symlink dump path: {given_dump}")
    dump_path = given_dump.resolve()
    commands: list[str] = []

    if recreate_target:
        commands.append(f"DROP DATABASE IF EXISTS {quoted_target}")
        commands.append(f"CREATE DATABASE {quoted_target}")
    else:
        commands.append(f"CREATE DATABASE {quoted_target}")
    commands.append(f"gunzip -c {shlex.quote(str(dump_path))} | mariadb {target_database}")
    cleanup_command = None
    if cleanup_after_success and classify_database(target_database).role == DatabaseRole.RESTORE_CHECK_TEMP:
        cleanup_command = f"DROP DATABASE IF EXISTS {quoted_target}"

    if not execute:
        return RestoreExecutionResult(
            source_database=source_database,
            target_database=target_database,
            dump_path=str(dump_path),
            dry_run=True,
            message=f"Would restore {source_database} backup into {target_database}.",
            commands=commands,
            cleanup_command=cleanup_command,
        )

    ordinary_live_dev_reset = (
        execute
        and recreate_target
        and classify_database(target_database).role == DatabaseRole.DEVELOPMENT
        and not (
            governed_destination_rehearsal
            or governed_production_cutover
            or governed_destination_recovery
        )
    )
    ordinary_restore_check = (
        execute
        and recreate_target
        and cleanup_after_success
        and classify_database(target_database).role == DatabaseRole.RESTORE_CHECK_TEMP
        and not governed_destination_rehearsal
    )
    dedicated_restore_lane_required = ordinary_live_dev_reset or ordinary_restore_check
    # Ordinary dev resets and disposable restore-checks must never substitute
    # the general/source credential. Direct callers must resolve to the same
    # dedicated restore lane as the CLI and interactive workflows.
    if dedicated_restore_lane_required:
        try:
            dedicated_cfg = load_mariadb_restore_config()
        except MariaDbConfigError as exc:
            return RestoreExecutionResult(
                source_database=source_database,
                target_database=target_database,
                dump_path=str(dump_path),
                refused=True,
                message=(
                    "Restore preflight refused before target modification: dedicated "
                    f"[mariadb_restore] credentials are unavailable: {exc}"
                ),
                commands=commands,
                cleanup_command=cleanup_command,
            )
        if config is not None and config != dedicated_cfg:
            return RestoreExecutionResult(
                source_database=source_database,
                target_database=target_database,
                dump_path=str(dump_path),
                refused=True,
                message=(
                    "Restore refused before target modification: supplied credential "
                    "does not match dedicated [mariadb_restore] configuration."
                ),
                commands=commands,
                cleanup_command=cleanup_command,
            )
        config = dedicated_cfg

    if ordinary_restore_check and restore_preflight is None:
        from mercury.sync.restore_preflight import evaluate_restore_privileges

        restore_preflight = evaluate_restore_privileges(
            source=source_database,
            target=target_database,
            backup_dir=dump_path.parent,
            config=config,
            receipt_root=receipt_root,
            schema_rewrites=dict(schema_rewrites or {}),
        )

    if require_restore_preflight or ordinary_live_dev_reset or ordinary_restore_check:
        issues: list[str] = []
        if restore_preflight is None or not getattr(restore_preflight, "passed", False):
            issues.append("a passed restore privilege preflight is required")
            if restore_preflight is not None:
                issues.extend(getattr(restore_preflight, "missing_capabilities", []))
                issues.extend(getattr(restore_preflight, "inspection_issues", []))
        else:
            if restore_preflight.source_database != source_database:
                issues.append("preflight source does not match restore source")
            if restore_preflight.target_database != target_database:
                issues.append("preflight target does not match restore target")
            if restore_preflight.artifact_file != dump_path.name:
                issues.append("preflight artifact does not match restore dump")
            if restore_preflight.artifact_sha256 != sha256_file(dump_path):
                issues.append("preflight artifact checksum does not match restore dump")
            if not getattr(restore_preflight, "backup_id", ""):
                issues.append("preflight backup identity is missing")
            manifest_path = dump_path.parent / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if restore_preflight.backup_id != manifest.get("backup_id"):
                    issues.append("preflight backup ID does not match restore manifest")
                if manifest.get("sha256") != restore_preflight.artifact_sha256:
                    issues.append("preflight checksum does not match restore manifest")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                issues.append(f"restore manifest cannot be bound to preflight: {exc}")
            if not getattr(restore_preflight, "evidence_written", False):
                issues.append("preflight evidence receipt was not written")
            expected_identity = getattr(restore_preflight, "current_user", None)
            if not expected_identity:
                issues.append("preflight restore identity is missing")
            else:
                try:
                    identity_cfg = (
                        config
                        or (load_mariadb_restore_config() if dedicated_restore_lane_required else None)
                        or try_load_mariadb_config()
                        or load_mariadb_config()
                    )
                    actual_identity = run_client_query(identity_cfg, "SELECT CURRENT_USER()").strip()
                    if actual_identity != expected_identity:
                        issues.append("preflight restore identity does not match import identity")
                except Exception as exc:
                    issues.append(f"restore identity cannot be verified before modification: {exc}")
        if issues:
            return RestoreExecutionResult(
                source_database=source_database,
                target_database=target_database,
                dump_path=str(dump_path),
                refused=True,
                message="Restore refused before target modification: " + "; ".join(issues),
                commands=commands,
                cleanup_command=cleanup_command,
            )

    if not (governed_destination_rehearsal or governed_production_cutover):
        from mercury.storage.host_maintenance import refuse_if_hdd_writes_disabled

        try:
            refuse_if_hdd_writes_disabled("restore-check evidence write")
        except RuntimeError as exc:
            return RestoreExecutionResult(
                source_database=source_database,
                target_database=target_database,
                dump_path=str(dump_path),
                dry_run=False,
                executed=False,
                refused=True,
                message=str(exc),
                commands=commands,
                cleanup_command=cleanup_command,
            )

    live_allowed = (
        (not resolved.dry_run) and resolved.live_actions_enabled
        if governed_destination_rehearsal or governed_production_cutover or governed_destination_recovery
        else resolved.live_execution_allowed()
    )
    if not live_allowed:
        reason = (
            "Live actions are disabled for the governed destination operation."
            if governed_destination_rehearsal or governed_production_cutover
            else resolved.refusal_reason() or "Live restore is not permitted."
        )
        result = RestoreExecutionResult(
            source_database=source_database,
            target_database=target_database,
            dump_path=str(dump_path),
            refused=True,
            message=reason,
            commands=commands,
            cleanup_command=cleanup_command,
        )
        from mercury.state.ledger import record_restore_check_result

        receipt = record_restore_check_result(result, state_root=receipt_root)
        result.receipt_path = str(receipt) if receipt else None
        return result

    cfg = config or try_load_mariadb_config()
    if cfg is None:
        cfg = load_mariadb_config()

    import_argv = build_import_argv(cfg, target_database)
    runner = import_runner or _make_import_runner(
        source_database,
        target_database,
        schema_rewrites=schema_rewrites,
        on_progress=on_import_progress,
    )

    target_created = False
    try:
        _assert_regular_dump_file(dump_path)
        if recreate_target:
            _execute_client_sql(cfg, f"DROP DATABASE IF EXISTS {quoted_target}")
        _execute_client_sql(cfg, f"CREATE DATABASE {quoted_target}")
        target_created = True
        # The production-cutover lane uses this callback to record rollback
        # ownership before the first dump statement is streamed.  A dump can
        # fail on its opening DROP TABLE after CREATE DATABASE succeeds.
        if on_target_created is not None:
            on_target_created(target_database)
        with client_process_credentials(cfg) as (env, extra):
            runner(
                prepend_client_defaults(import_argv, extra),
                env,
                dump_path,
                cfg,
                target_database,
            )
    except BackupExecutionError as exc:
        rollback_note = ""
        cleanup_dropped = False
        if rollback_new_target_on_failure and target_created:
            try:
                _execute_client_sql(cfg, f"DROP DATABASE {quoted_target}")
                cleanup_dropped = True
                rollback_note = " Newly created target was rolled back."
            except BackupExecutionError as rollback_exc:
                rollback_note = f" Rollback of newly created target failed: {rollback_exc}"
        result = RestoreExecutionResult(
            source_database=source_database,
            target_database=target_database,
            dump_path=str(dump_path),
            refused=True,
            message=(
                f"{exc}. Temporary restore-check database preserved for debugging."
                if cleanup_command
                else str(exc) + rollback_note
            ),
            commands=commands,
            cleanup_command=cleanup_command,
            cleanup_dropped=cleanup_dropped,
        )
        from mercury.state.ledger import record_restore_check_result

        receipt = record_restore_check_result(result, state_root=receipt_root)
        result.receipt_path = str(receipt) if receipt else None
        return result

    cleanup_dropped = False
    message = f"Restored {source_database} into {target_database}."
    verification_passed: bool | None = None
    verification_detail: str | None = None
    verification_issues: list[str] = []
    target_table_count: int | None = None

    manifest_path = dump_path.parent / "manifest.json"
    if manifest_path.is_file():
        post = _verify_restore_target(
            target_database,
            manifest_path=manifest_path,
            config=cfg,
            row_fn=inspect_row_fn,
        )
        verification_passed = post.verified
        verification_detail = post.detail
        verification_issues = list(post.issues)
        target_table_count = post.table_count
        if not post.verified:
            issue_text = "; ".join(post.issues) if post.issues else "post-import verification failed"
            if cleanup_command:
                message = (
                    f"Imported {source_database} into {target_database}, but restore-check verification failed: "
                    f"{issue_text}. Temporary restore-check database preserved for debugging."
                )
            else:
                message = (
                    f"Imported {source_database} into {target_database}, but target verification failed: "
                    f"{issue_text}."
                )

    if cleanup_command and verification_passed is not False:
        try:
            _execute_client_sql(cfg, cleanup_command)
            cleanup_dropped = True
            message = (
                f"Restored {source_database} into {target_database} and dropped the temporary restore-check database."
            )
        except BackupExecutionError:
            message = (
                f"Restored {source_database} into {target_database}, but automatic cleanup failed. "
                f"Run: {cleanup_command}"
            )

    result = RestoreExecutionResult(
        source_database=source_database,
        target_database=target_database,
        dump_path=str(dump_path),
        dry_run=False,
        executed=True,
        message=message,
        commands=commands,
        cleanup_dropped=cleanup_dropped,
        cleanup_command=cleanup_command,
        verification_passed=verification_passed,
        verification_detail=verification_detail,
        verification_issues=verification_issues,
        target_table_count=target_table_count,
    )
    from mercury.state.ledger import record_restore_check_result

    receipt = record_restore_check_result(result, state_root=receipt_root)
    result.receipt_path = str(receipt) if receipt else None
    return result
