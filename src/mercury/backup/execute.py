"""Production-safe backup execution (defaults to dry-run)."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.layout import (
    CHECKSUM_FILENAME,
    MANIFEST_FILENAME,
    REPORT_FILENAME,
    build_backup_layout,
)
from mercury.backup.manifest import BackupKind, BackupManifest, build_backup_manifest
from mercury.backup.checksum import sha256_file, write_checksum_file
from mercury.database.core import DatabaseClassification, DatabaseRole, classify_database
from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config
from mercury.database.mariadb.session import resolve_mariadb_target, try_load_mariadb_config
from mercury.backup.dump_planner import (
    DumpKind,
    build_dump_argv_for_config,
    build_planned_dump,
    select_dump_tool,
)
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.backup.manifest_preview import exclusion_reason
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

DumpRunner = Callable[
    [list[str], dict[str, str], Path, MariaDbConnectionConfig],
    None,
]


class BackupExecutionError(Exception):
    """Backup cannot proceed safely."""


class BackupExecutionResult(BaseModel):
    database: str
    backup_kind: BackupKind
    dry_run: bool
    executed: bool
    refused: bool = False
    refusal_reason: str | None = None
    backup_directory: str
    backup_directory_path: str | None = None
    dump_file: str | None = None
    schema_file: str | None = None
    manifest_file: str | None = None
    checksum_file: str | None = None
    report_file: str | None = None
    command: str = ""
    schema_command: str | None = None
    tool_used: str = "mariadb-dump"
    manifest: BackupManifest | None = None
    live_actions_enabled: bool = False
    safety_notes: list[str] = Field(default_factory=list)


def assert_safe_backup_source(database: str) -> DatabaseClassification:
    """Refuse backup when database is not an approved backup source."""
    classification = classify_database(database)
    if not classification.backup_source:
        reason = exclusion_reason(classification) or "Not a backup source."
        raise BackupExecutionError(
            f"Refusing backup for '{database}': {reason}"
        )
    return classification


def assert_not_production_restore_target(
    database: str,
    *,
    operation: str = "restore",
) -> None:
    """Block write/restore/sync operations that would target production."""
    classification = classify_database(database)
    if classification.role == DatabaseRole.PRODUCTION:
        raise BackupExecutionError(
            f"Refusing {operation} into production database '{database}'. "
            "Production databases must never be overwritten or restored into by default."
        )
    if classification.role == DatabaseRole.SHARED_AUTHORITY and operation == "restore":
        raise BackupExecutionError(
            f"Refusing {operation} into shared authority database '{database}'."
        )


def _default_dump_runner(
    argv: list[str],
    env: dict[str, str],
    output_path: Path,
    _config: MariaDbConnectionConfig,
) -> None:
    """Run mariadb-dump piped to gzip -c."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        dump_proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert dump_proc.stdout is not None
        gzip_proc = subprocess.Popen(
            ["gzip", "-c"],
            stdin=dump_proc.stdout,
            stdout=handle,
            stderr=subprocess.PIPE,
            env=env,
        )
        dump_proc.stdout.close()
        gzip_stderr = gzip_proc.communicate()[1]
        dump_stderr = dump_proc.communicate()[1]
        if dump_proc.returncode != 0:
            detail = (dump_stderr or b"").decode("utf-8", errors="replace").strip()
            raise BackupExecutionError(
                f"mariadb-dump failed (exit {dump_proc.returncode}): {detail or 'unknown error'}"
            )
        if gzip_proc.returncode != 0:
            detail = (gzip_stderr or b"").decode("utf-8", errors="replace").strip()
            raise BackupExecutionError(
                f"gzip failed (exit {gzip_proc.returncode}): {detail or 'unknown error'}"
            )


def _command_display(argv: list[str]) -> str:
    return " ".join(argv)


def _artifact_filenames(
    layout,
    kind: BackupKind,
) -> tuple[str | None, str | None]:
    if kind == BACKUP_KIND_SCHEMA_ONLY:
        return None, layout.schema_dump_file
    return layout.full_dump_file, layout.schema_dump_file


def plan_backup_execution(
    database: str,
    kind: BackupKind,
    *,
    policy: ExecutionPolicy | None = None,
    date: str | None = None,
    timestamp: str | None = None,
) -> BackupExecutionResult:
    """Build a backup execution plan without writing files or contacting the server."""
    classification = assert_safe_backup_source(database)
    resolved = policy or load_execution_policy()
    layout = build_backup_layout(database, date=date, timestamp=timestamp)
    dump_name, schema_name = _artifact_filenames(layout, kind)

    mariadb_cfg = try_load_mariadb_config()
    planned = (
        build_planned_dump(
            database,
            kind,
            host=mariadb_cfg.host if mariadb_cfg else "localhost",
            port=mariadb_cfg.port if mariadb_cfg else 3306,
            user=mariadb_cfg.user if mariadb_cfg else "USER",
            unix_socket=mariadb_cfg.unix_socket if mariadb_cfg else None,
        )
        if mariadb_cfg
        else build_planned_dump(database, kind)
    )
    schema_planned = None
    if kind == BACKUP_KIND_FULL:
        schema_planned = (
            build_planned_dump(
                database,
                BACKUP_KIND_SCHEMA_ONLY,
                host=mariadb_cfg.host,
                port=mariadb_cfg.port,
                user=mariadb_cfg.user,
                unix_socket=mariadb_cfg.unix_socket,
            )
            if mariadb_cfg
            else build_planned_dump(database, BACKUP_KIND_SCHEMA_ONLY)
        )

    refusal = None if resolved.live_execution_allowed() else resolved.refusal_reason()

    return BackupExecutionResult(
        database=database,
        backup_kind=kind,
        dry_run=resolved.dry_run or not resolved.live_actions_enabled,
        executed=False,
        refused=refusal is not None,
        refusal_reason=refusal,
        backup_directory=layout.directory,
        dump_file=dump_name,
        schema_file=schema_name,
        manifest_file=layout.manifest_path(),
        checksum_file=layout.checksum_path(),
        report_file=layout.report_path(),
        command=planned.command,
        schema_command=schema_planned.command if schema_planned else None,
        live_actions_enabled=resolved.live_actions_enabled,
        safety_notes=[
            f"Source role: {classification.role.value}",
            "Backup reads from source database only; never restores into *_prod.",
            "Dry-run by default unless live execution is explicitly enabled.",
        ],
    )


def execute_backup(
    database: str,
    kind: BackupKind,
    *,
    execute: bool = False,
    policy: ExecutionPolicy | None = None,
    date: str | None = None,
    timestamp: str | None = None,
    mariadb_config: MariaDbConnectionConfig | None = None,
    dump_runner: DumpRunner | None = None,
    now: datetime | None = None,
) -> BackupExecutionResult:
    """
    Plan or execute a logical backup.

    When execute=False (default), returns a dry-run plan and writes nothing.
    When execute=True, runs mariadb-dump only if live execution is permitted.
    """
    classification = assert_safe_backup_source(database)
    resolved = policy or load_execution_policy()
    instant = now or datetime.now(timezone.utc)
    layout = build_backup_layout(database, date=date, timestamp=timestamp, now=instant)
    dump_name, schema_name = _artifact_filenames(layout, kind)

    backup_dir = resolved.backup_root / layout.date / database
    relative_dir = layout.directory

    config = mariadb_config or try_load_mariadb_config()

    try:
        tool = select_dump_tool()
    except RuntimeError as exc:
        if execute and resolved.live_execution_allowed():
            raise BackupExecutionError(str(exc)) from exc
        tool = "mariadb-dump"

    if config is None and execute and resolved.live_execution_allowed():
        config = load_mariadb_config()

    argv: list[str] = []
    schema_argv: list[str] | None = None
    if config is not None:
        argv = build_dump_argv_for_config(database, kind, config, tool=tool)
        if kind == BACKUP_KIND_FULL and schema_name:
            schema_argv = build_dump_argv_for_config(
                database, BACKUP_KIND_SCHEMA_ONLY, config, tool=tool
            )
    else:
        host, port, user = resolve_mariadb_target(None)
        from mercury.backup.dump_planner import build_dump_argv

        argv = build_dump_argv(database, kind, host=host, port=port, user=user, tool=tool)
        if kind == BACKUP_KIND_FULL and schema_name:
            schema_argv = build_dump_argv(
                database, BACKUP_KIND_SCHEMA_ONLY, host=host, port=port, user=user, tool=tool
            )

    base_result = BackupExecutionResult(
        database=database,
        backup_kind=kind,
        dry_run=True,
        executed=False,
        backup_directory=relative_dir,
        backup_directory_path=str(backup_dir),
        dump_file=dump_name,
        schema_file=schema_name,
        manifest_file=layout.manifest_path(),
        checksum_file=layout.checksum_path(),
        report_file=layout.report_path(),
        command=_command_display(argv),
        schema_command=_command_display(schema_argv) if schema_argv else None,
        tool_used=tool,
        live_actions_enabled=resolved.live_actions_enabled,
        safety_notes=[
            f"Source role: {classification.role.value}",
            "Backup reads from source database only; never restores into *_prod.",
        ],
    )

    if not execute:
        base_result.dry_run = True
        base_result.refused = False
        base_result.refusal_reason = resolved.refusal_reason()
        base_result.safety_notes.append("Dry-run plan only; no files written.")
        return base_result

    refusal = resolved.refusal_reason()
    if refusal:
        base_result.refused = True
        base_result.refusal_reason = refusal
        base_result.safety_notes.append(refusal)
        return base_result

    if config is None:
        config = load_mariadb_config()

    runner = dump_runner or _default_dump_runner
    env = os.environ.copy()
    if config.password:
        env["MYSQL_PWD"] = config.password

    backup_dir.mkdir(parents=True, exist_ok=True)
    checksum_targets: list[str] = []

    primary_path: Path | None = None
    schema_path: Path | None = None

    if kind == BACKUP_KIND_SCHEMA_ONLY:
        assert schema_name is not None
        schema_path = backup_dir / schema_name
        runner(argv, env, schema_path, config)
        checksum_targets.append(schema_name)
        primary_path = schema_path
    else:
        assert dump_name is not None
        dump_path = backup_dir / dump_name
        runner(argv, env, dump_path, config)
        checksum_targets.append(dump_name)
        primary_path = dump_path
        if schema_name and schema_argv:
            schema_path = backup_dir / schema_name
            runner(schema_argv, env, schema_path, config)
            checksum_targets.append(schema_name)

    write_checksum_file(backup_dir, checksum_targets)

    primary_sha = sha256_file(primary_path)
    primary_size = primary_path.stat().st_size
    schema_sha = sha256_file(schema_path) if schema_path else None
    schema_size = schema_path.stat().st_size if schema_path else None

    manifest_dump_file = schema_name if kind == BACKUP_KIND_SCHEMA_ONLY else dump_name
    assert manifest_dump_file is not None

    manifest = build_backup_manifest(
        backup_id=f"{database}-{kind}-{layout.timestamp}",
        database=database,
        backup_kind=kind,
        created_at=instant,
        source_role=classification.role.value,
        dump_file=manifest_dump_file,
        dump_sha256=primary_sha if kind != BACKUP_KIND_FULL else primary_sha,
        dump_size_bytes=primary_size,
        schema_file=schema_name if kind == BACKUP_KIND_FULL else None,
        schema_sha256=schema_sha if kind == BACKUP_KIND_FULL else None,
        schema_size_bytes=schema_size if kind == BACKUP_KIND_FULL else None,
        tool_used=tool,
        live_actions_enabled=resolved.live_actions_enabled,
        dry_run=False,
        notes="Logical backup produced by Mercury.",
        verified=False,
    )

    manifest_path = backup_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    report_path = backup_dir / REPORT_FILENAME
    report_path.write_text(
        _format_backup_report(manifest, relative_dir),
        encoding="utf-8",
    )

    return base_result.model_copy(
        update={
            "dry_run": False,
            "executed": True,
            "refused": False,
            "refusal_reason": None,
            "manifest": manifest,
            "safety_notes": base_result.safety_notes
            + ["Backup artifacts written; verification required before protected status."],
        }
    )


def _format_backup_report(manifest: BackupManifest, relative_dir: str) -> str:
    lines = [
        "# Mercury Backup Report",
        "",
        f"- **Database:** {manifest.database}",
        f"- **Backup kind:** {manifest.backup_kind}",
        f"- **Created:** {manifest.created_at}",
        f"- **Directory:** {relative_dir}",
        f"- **Dump file:** {manifest.dump_file}",
        f"- **Schema file:** {manifest.schema_file or 'n/a'}",
        f"- **Verified:** {manifest.verified}",
        f"- **Live actions enabled:** {manifest.live_actions_enabled}",
        "",
        manifest.notes,
        "",
    ]
    return "\n".join(lines)
