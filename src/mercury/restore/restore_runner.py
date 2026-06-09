"""Restore verified logical backups into dev or restore-check targets."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.backup_runner import BackupExecutionError, assert_not_production_restore_target
from mercury.database.core import DatabaseRole, classify_database
from mercury.database.mariadb.client import run_client_sql, select_client_tool
from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.session import try_load_mariadb_config
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy

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


def assert_safe_restore_target(database: str) -> None:
    """Only disposable dev targets and _restorecheck_* temp databases."""
    assert_not_production_restore_target(database, operation="restore")
    role = classify_database(database).role
    if role in {DatabaseRole.DEVELOPMENT, DatabaseRole.RESTORE_CHECK_TEMP}:
        return
    raise BackupExecutionError(
        f"Refusing restore into '{database}': only *_dev and _restorecheck_* targets are allowed."
    )


def build_import_argv(config: MariaDbConnectionConfig, database: str) -> list[str]:
    tool = select_client_tool()
    argv = [tool, "-u", config.user, database]
    if config.unix_socket:
        argv[1:1] = [f"--socket={config.unix_socket}", "--protocol=SOCKET"]
    else:
        argv[1:1] = ["-h", config.host, "-P", str(config.port)]
        if config.ssl_disabled:
            argv[1:1] = ["--skip-ssl"]
    return argv


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


def _default_import_runner(
    argv: list[str],
    env: dict[str, str],
    dump_path: Path,
    _config: MariaDbConnectionConfig,
    _target: str,
) -> None:
    from mercury.database.mariadb.import_stream import run_compressed_sql_import

    run_compressed_sql_import(argv, env, dump_path, strip_definer=True)


def execute_restore_into_database(
    *,
    target_database: str,
    dump_path: Path,
    source_database: str,
    execute: bool = False,
    policy: ExecutionPolicy | None = None,
    recreate_target: bool = True,
    cleanup_after_success: bool = False,
    config: MariaDbConnectionConfig | None = None,
    import_runner: ImportRunner | None = None,
) -> RestoreExecutionResult:
    """Plan or run ``gunzip -c dump | mariadb target`` for verified backups."""
    assert_safe_restore_target(target_database)
    resolved = policy or load_execution_policy()
    dump_path = dump_path.resolve()
    commands: list[str] = []

    if recreate_target:
        commands.append(f"DROP DATABASE IF EXISTS `{target_database}`")
        commands.append(f"CREATE DATABASE `{target_database}`")
    else:
        commands.append(f"CREATE DATABASE IF NOT EXISTS `{target_database}`")
    commands.append(f"gunzip -c {dump_path} | mariadb {target_database}")
    cleanup_command = None
    if cleanup_after_success and classify_database(target_database).role == DatabaseRole.RESTORE_CHECK_TEMP:
        cleanup_command = f"DROP DATABASE IF EXISTS `{target_database}`"

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

    if not resolved.live_execution_allowed():
        reason = resolved.refusal_reason() or "Live restore is not permitted."
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

        record_restore_check_result(result)
        return result

    cfg = config or try_load_mariadb_config()
    if cfg is None:
        cfg = load_mariadb_config()

    import_argv = build_import_argv(cfg, target_database)
    runner = import_runner or _default_import_runner

    try:
        if recreate_target:
            _execute_client_sql(cfg, f"DROP DATABASE IF EXISTS `{target_database}`")
        _execute_client_sql(cfg, f"CREATE DATABASE `{target_database}`")
        runner(import_argv, _client_env(cfg), dump_path, cfg, target_database)
    except BackupExecutionError as exc:
        result = RestoreExecutionResult(
            source_database=source_database,
            target_database=target_database,
            dump_path=str(dump_path),
            refused=True,
            message=(
                f"{exc}. Temporary restore-check database preserved for debugging."
                if cleanup_command
                else str(exc)
            ),
            commands=commands,
            cleanup_command=cleanup_command,
        )
        from mercury.state.ledger import record_restore_check_result

        record_restore_check_result(result)
        return result

    cleanup_dropped = False
    message = f"Restored {source_database} into {target_database}."
    if cleanup_command:
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
    )
    from mercury.state.ledger import record_restore_check_result

    record_restore_check_result(result)
    return result
