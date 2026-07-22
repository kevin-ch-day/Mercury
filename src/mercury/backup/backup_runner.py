"""Production-safe backup execution (defaults to dry-run)."""

from __future__ import annotations

import gzip
import json
import os
import shutil
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
from mercury.backup.content_contract import (
    BackupContentContract,
    build_backup_content_contract,
    extract_dump_object_inventory,
    fetch_live_object_inventory,
)
from mercury.backup.live_inventory import (
    fetch_live_server_database_names,
    live_source_missing_reason,
)
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy
from mercury.core.artifact_permissions import ensure_private_directory, restrict_artifact_file
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
    content_contract: BackupContentContract | None = None


def assert_safe_backup_source(
    database: str, *, allow_development_backup: bool = False
) -> DatabaseClassification:
    """Refuse backup when database is not an approved backup source."""
    classification = classify_database(database)
    if (
        allow_development_backup
        and classification.role == DatabaseRole.DEVELOPMENT
    ):
        from mercury.database.core.scope import is_active_dev_recovery_database

        if is_active_dev_recovery_database(database):
            return classification
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
    """Run mariadb-dump, compressing with gzip CLI or Python gzip."""
    ensure_private_directory(output_path.parent)
    if shutil.which("gzip"):
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
        return

    result = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BackupExecutionError(
            f"mariadb-dump failed (exit {result.returncode}): {detail or 'unknown error'}"
        )
    with gzip.open(output_path, "wb") as handle:
        handle.write(result.stdout or b"")


def _command_display(argv: list[str]) -> str:
    return " ".join(argv)


def _temp_artifact_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _artifact_filenames(
    layout,
    kind: BackupKind,
) -> tuple[str | None, str | None]:
    if kind == BACKUP_KIND_SCHEMA_ONLY:
        return None, layout.schema_dump_file
    return layout.full_dump_file, layout.schema_dump_file


def _missing_source_refusal(
    database: str,
    *,
    live: bool,
    server_names: set[str] | None,
) -> str | None:
    return live_source_missing_reason(database, live=live, server_names=server_names)


def plan_backup_execution(
    database: str,
    kind: BackupKind,
    *,
    policy: ExecutionPolicy | None = None,
    date: str | None = None,
    timestamp: str | None = None,
    live: bool = False,
    server_names: set[str] | None = None,
    allow_development_backup: bool = False,
) -> BackupExecutionResult:
    """Build a backup execution plan without writing files or contacting the server."""
    classification = assert_safe_backup_source(database, allow_development_backup=allow_development_backup)
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

    resolved_server_names = server_names
    if live and resolved_server_names is None:
        resolved_server_names = fetch_live_server_database_names()
    missing_reason = _missing_source_refusal(
        database,
        live=live,
        server_names=resolved_server_names,
    )
    if missing_reason:
        refusal = missing_reason
    else:
        refusal = None

    return BackupExecutionResult(
        database=database,
        backup_kind=kind,
        dry_run=True,
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
            "Preview plan only; use execute=True or menu/CLI backup run to write files.",
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
    live: bool = False,
    server_names: set[str] | None = None,
    allow_development_backup: bool = False,
) -> BackupExecutionResult:
    """
    Plan or execute a logical backup.

    When execute=False, returns a preview plan and writes nothing.
    When execute=True, runs mariadb-dump when the backup environment is valid.
    """
    from mercury.logging import get_logger

    log = get_logger("mercury.backup")
    log.info(
        "backup start database=%s kind=%s execute=%s",
        database,
        kind,
        execute,
    )
    classification = assert_safe_backup_source(database, allow_development_backup=allow_development_backup)
    resolved = policy or load_execution_policy()
    instant = now or datetime.now(timezone.utc)
    layout = build_backup_layout(database, date=date, timestamp=timestamp, now=instant)
    dump_name, schema_name = _artifact_filenames(layout, kind)

    # Layout paths include the immutable run timestamp.  Do not collapse this
    # to date/database: that would overwrite a prior run's manifest, checksum,
    # and report when more than one backup is made on the same day.
    backup_dir = resolved.backup_root / layout.date / database / layout.timestamp
    relative_dir = layout.directory

    config = mariadb_config or try_load_mariadb_config()

    try:
        tool = select_dump_tool()
    except RuntimeError as exc:
        if execute and resolved.backup_execution_allowed():
            raise BackupExecutionError(str(exc)) from exc
        tool = "mariadb-dump"

    if config is None and execute and resolved.backup_execution_allowed():
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
            *( ["Explicit development recovery backup; excluded from routine production protection."] if allow_development_backup else []),
        ],
    )

    resolved_server_names = server_names
    if live and resolved_server_names is None:
        resolved_server_names = fetch_live_server_database_names()

    if not execute:
        missing_reason = _missing_source_refusal(
            database,
            live=live,
            server_names=resolved_server_names,
        )
        base_result.dry_run = True
        base_result.refused = missing_reason is not None
        base_result.refusal_reason = missing_reason
        base_result.safety_notes.append("Preview plan only; no files written.")
        if missing_reason:
            base_result.safety_notes.append(missing_reason)
        log.info("backup preview database=%s kind=%s", database, kind)
        return base_result

    missing_reason = _missing_source_refusal(
        database,
        live=live,
        server_names=resolved_server_names,
    )
    if missing_reason:
        refusal = missing_reason
    elif not resolved.backup_execution_allowed():
        refusal = resolved.backup_refusal_reason()
    else:
        from mercury.storage.host_maintenance import writes_allowed

        if not writes_allowed():
            refusal = (
                "Mercury writes are disabled for safe HDD detach "
                "(host maintenance writes_allowed=false). "
                "Use Storage Operations → Reconnect / Validate Mercury HDD to restore writes."
            )
        else:
            refusal = None
    if refusal:
        base_result.refused = True
        base_result.refusal_reason = refusal
        base_result.safety_notes.append(refusal)
        log.warning("backup refused database=%s reason=%s", database, refusal)
        return base_result

    if config is None:
        config = load_mariadb_config()

    # A live backup is not accepted solely because mariadb-dump exited zero.
    # Record the source object set before writing artifacts, then require the
    # emitted full dump (and its schema companion) to preserve that set.
    live_inventory = None
    if live:
        try:
            live_inventory = fetch_live_object_inventory(config, database)
        except Exception as exc:
            raise BackupExecutionError(
                "Could not establish the live backup object contract; "
                "refusing to create an unverifiable backup."
            ) from exc

    runner = dump_runner or _default_dump_runner
    env = os.environ.copy()
    if config.password:
        env["MYSQL_PWD"] = config.password

    ensure_private_directory(backup_dir)
    checksum_targets: list[str] = []

    primary_path: Path | None = None
    schema_path: Path | None = None
    created_paths: list[Path] = []
    content_contract: BackupContentContract | None = None

    try:
        if kind == BACKUP_KIND_SCHEMA_ONLY:
            assert schema_name is not None
            schema_path = backup_dir / schema_name
            schema_temp = _temp_artifact_path(schema_path)
            runner(argv, env, schema_temp, config)
            schema_temp.replace(schema_path)
            created_paths.append(schema_path)
            checksum_targets.append(schema_name)
            primary_path = schema_path
        else:
            assert dump_name is not None
            dump_path = backup_dir / dump_name
            dump_temp = _temp_artifact_path(dump_path)
            runner(argv, env, dump_temp, config)
            dump_temp.replace(dump_path)
            created_paths.append(dump_path)
            checksum_targets.append(dump_name)
            primary_path = dump_path
            if schema_name and schema_argv:
                schema_path = backup_dir / schema_name
                schema_temp = _temp_artifact_path(schema_path)
                runner(schema_argv, env, schema_temp, config)
                schema_temp.replace(schema_path)
                created_paths.append(schema_path)
                checksum_targets.append(schema_name)

        if live_inventory is not None:
            assert primary_path is not None
            dump_inventory = extract_dump_object_inventory(primary_path)
            schema_inventory = (
                extract_dump_object_inventory(schema_path)
                if kind == BACKUP_KIND_FULL and schema_path is not None
                else None
            )
            content_contract = build_backup_content_contract(
                live_inventory,
                dump_inventory,
                schema_inventory,
            )
            if not content_contract.verified:
                detail = "; ".join(content_contract.issues[:3])
                raise BackupExecutionError(
                    "Backup content contract failed; artifacts were not accepted: "
                    f"{detail or 'unknown object mismatch'}"
                )

        checksum_path = backup_dir / CHECKSUM_FILENAME
        checksum_temp = backup_dir / f"{CHECKSUM_FILENAME}.tmp"
        write_checksum_file(backup_dir, checksum_targets, output_path=checksum_temp)
        checksum_temp.replace(checksum_path)
        created_paths.append(checksum_path)

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
            live_actions_enabled=resolved.backup_execution_allowed(),
            dry_run=False,
            notes="Logical backup produced by Mercury.",
            verified=False,
            dump_options=argv,
            object_contract=(
                content_contract.model_dump(mode="json")
                if content_contract is not None
                else None
            ),
        )

        manifest_path = backup_dir / MANIFEST_FILENAME
        manifest_temp = backup_dir / f"{MANIFEST_FILENAME}.tmp"
        manifest_temp.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        manifest_temp.replace(manifest_path)
        created_paths.append(manifest_path)

        report_path = backup_dir / REPORT_FILENAME
        report_temp = backup_dir / f"{REPORT_FILENAME}.tmp"
        report_temp.write_text(
            _format_backup_report(manifest, relative_dir),
            encoding="utf-8",
        )
        report_temp.replace(report_path)
        created_paths.append(report_path)
        for artifact in created_paths:
            restrict_artifact_file(artifact)
    except Exception:
        cleanup_candidates = created_paths + list(backup_dir.glob("*.tmp"))
        for path in cleanup_candidates:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        raise

    result = base_result.model_copy(
        update={
            "dry_run": False,
            "executed": True,
            "refused": False,
            "refusal_reason": None,
            "manifest": manifest,
            "content_contract": content_contract,
            "safety_notes": base_result.safety_notes
            + ["Backup artifacts written; verification required before protected status."],
        }
    )
    log.info(
        "backup executed database=%s kind=%s backup_id=%s directory=%s",
        database,
        kind,
        manifest.backup_id,
        relative_dir,
    )
    from mercury.state.ledger import record_backup_execution

    record_backup_execution(result)
    return result


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
