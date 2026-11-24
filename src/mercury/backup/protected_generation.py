"""Opt-in verified atomic database generations using Mercury's maintained dumper."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from mercury.backup.backup_runner import BackupExecutionError, assert_safe_backup_source, execute_backup
from mercury.backup.durability import durable_replace_text, sync_directory, sync_tree
from mercury.backup.layout import MANIFEST_FILENAME
from mercury.backup.verification import BackupVerificationResult, verify_backup_artifacts
from mercury.core.execution_policy import load_execution_policy
from mercury.core.storage_roots import load_storage_config
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.storage.verified_volume import (
    activate_disk,
    exclusive_lock,
    require_destination,
    require_headroom,
)


def _publish_generation(
    directory: Path,
    final: Path,
    *,
    database: str,
    require_complete_full: bool,
    guard: Callable[[Path], None],
) -> BackupVerificationResult:
    """Reserve the final identity exclusively, publishing its manifest last.

    Directory rename can replace a concurrently created *empty* directory on
    POSIX. Exclusive mkdir avoids that race with the ordinary backup writer.
    A failed publication retains both its reservation and remaining staging.
    """
    artifacts = list(directory.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in artifacts):
        raise BackupExecutionError("Backup generation contains non-regular artifacts")
    final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        final.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise BackupExecutionError("Refusing to overwrite an existing backup generation") from exc
    manifest_path = directory / MANIFEST_FILENAME
    completion_path = directory / "COMPLETION.json"
    for artifact in artifacts:
        if artifact in (manifest_path, completion_path):
            continue
        artifact.rename(final / artifact.name)
    guard(final)
    verification = verify_backup_artifacts(
        final,
        database=database,
        require_complete_full=require_complete_full,
        staged_manifest_path=manifest_path,
    )
    if not verification.verified:
        raise BackupExecutionError("Backup failed final artifact verification before publication")
    completion_path.rename(final / completion_path.name)
    sync_tree(final)
    guard(final)
    published_manifest = final / MANIFEST_FILENAME
    manifest_path.rename(published_manifest)
    sync_directory(final)
    sync_directory(final.parent)
    return verification.model_copy(update={"manifest_path": str(published_manifest)})


def scoped_credentials(path: Path, database: str) -> MariaDbConnectionConfig:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_size > 16384
    ):
        raise ValueError(
            "Scoped credential file requires owned regular mode 0600, at most 16 KiB"
        )
    x = json.loads(path.read_text())
    if (
        set(x) != {"schema", "socket", "user", "password", "role"}
        or x["schema"] != database
        or not Path(x["socket"]).is_absolute()
    ):
        raise ValueError(
            "Scoped credentials must name the exact backup schema and absolute socket"
        )
    return MariaDbConnectionConfig(
        host="localhost",
        user=x["user"],
        password=x["password"],
        unix_socket=x["socket"],
        use_client=True,
    )


def execute_protected_backup(
    database: str,
    *,
    credentials: Path | None = None,
    policy=None,
    config=None,
    tables_only=False,
):
    """Retain incomplete work; advertise only a hash-verified, fsynced generation.

    This Linux ext4 lane does not change the existing cross-platform backup API.
    Source consistency is mariadb-dump --single-transaction; no concurrent DDL
    is permitted. Table/object metadata is checked separately by the dumper.
    """
    assert_safe_backup_source(database)
    if tables_only and database != "noctivar":
        raise BackupExecutionError("Explicit table-only preservation is restricted to noctivar")
    policy = policy or load_execution_policy()
    storage = load_storage_config().active_write_root
    mount, disk_uuid = str(storage.mount_path), storage.filesystem_uuid
    activate_disk(mount, disk_uuid)
    guard = lambda p: require_destination(mount, disk_uuid, p)
    guard(policy.backup_root)
    require_headroom(storage.mount_path, 2 * 1024**3)
    refusal = policy.backup_environment_refusal()
    if refusal:
        raise BackupExecutionError(refusal)
    if credentials:
        config = scoped_credentials(credentials, database)
    if config is None:
        from mercury.database.mariadb.config import load_mariadb_config

        config = load_mariadb_config()
    from mercury.backup.database_fingerprint import fingerprint
    from mercury.database.mariadb.session import fetch_user_database_names

    server_names = set(fetch_user_database_names(config))
    if database not in server_names:
        raise BackupExecutionError(
            "Selected backup account cannot see the exact schema"
        )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    state = policy.backup_root.parent / "mercury_backup_incomplete" / run_id
    guard(state)
    with exclusive_lock(Path(mount) / ".mercury-database-backup.lock"):
        state.mkdir(parents=True, mode=0o700)
        durable_replace_text(
            state / "STATE.json",
            json.dumps({"status": "incomplete", "database": database, "run_id": run_id})
            + "\n",
        )
        staging = state / "artifacts"
        staging.mkdir(mode=0o700)
        staged_policy = replace(policy, backup_root=staging)
        stage = "source_fingerprint_before"
        try:
            guard(staging)
            before = fingerprint(config, database)
            stage = "dump"
            result = execute_backup(
                database,
                "full",
                execute=True,
                live=True,
                tables_only=tables_only,
                nonlocking_schema=True,
                policy=staged_policy,
                mariadb_config=config,
                server_names=server_names,
                record_execution=False,
                preserve_failure=True,
            )
            if result.refused or not result.executed:
                raise BackupExecutionError(
                    result.refusal_reason or "Backup did not execute"
                )
            directory = Path(result.backup_directory_path)
            publication_root = (
                policy.backup_root.parent
                / "mercury_research_backups"
                / "noctivar-table-snapshots"
                if tables_only
                else policy.backup_root
            )
            final = publication_root / directory.relative_to(staging)
            stage = "source_fingerprint_after"
            after = fingerprint(config, database)
            if before != after:
                raise BackupExecutionError(
                    "Source changed during backup; retained for review, no semantic qualification"
                )
            stage = "verify_staged_generation"
            durable_replace_text(
                directory / "SOURCE_FINGERPRINT.json",
                json.dumps(before, indent=2) + "\n",
            )
            verification = verify_backup_artifacts(
                directory, database=database, require_complete_full=not tables_only
            )
            if not verification.verified:
                raise BackupExecutionError("New backup failed artifact verification")
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["verified"] = True
            durable_replace_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
            receipt = {
                "status": "tables_only_complete" if tables_only else "complete",
                "object_scope": "tables_only" if tables_only else "complete_schema",
                "run_id": run_id,
                "database": database,
                "created_at": datetime.now(UTC).isoformat(),
                "consistency": "mariadb-dump --single-transaction; InnoDB; concurrent DDL excluded",
                "tool_version": subprocess.check_output(
                    ["mariadb-dump", "--version"], text=True
                ).strip(),
                "verification": verification.model_copy(
                    update={"manifest_path": str(final / "manifest.json")}
                ).model_dump(mode="json"),
                "recovery": "Not restore-tested by backup creation; isolated rehearsal required",
            }
            durable_replace_text(
                directory / "COMPLETION.json", json.dumps(receipt, indent=2) + "\n"
            )
            stage = "publish_generation"
            sync_tree(directory)
            guard(final)
            published = _publish_generation(
                directory, final, database=database, require_complete_full=not tables_only, guard=guard
            )
            result = result.model_copy(
                update={
                    "backup_directory_path": str(final),
                    "manifest": result.manifest.model_copy(update={"verified": True}),
                }
            )
            from mercury.state.ledger import (
                record_backup_execution,
                record_backup_verification,
            )

            stage = "record_completion"
            if not tables_only:
                record_backup_execution(result)
                record_backup_verification(published)
            durable_replace_text(
                state / "STATE.json",
                json.dumps(
                    {
                        "status": "complete",
                        "database": database,
                        "run_id": run_id,
                        "published_directory": str(final),
                    }
                )
                + "\n",
            )
            return result
        except BaseException as exc:
            # Interruption leaves useful artifacts and never deletes old backups.
            try:
                guard(state)
                durable_replace_text(
                    state / "STATE.json",
                    json.dumps(
                        {
                            "status": "failed",
                            "database": database,
                            "run_id": run_id,
                            "stage": stage,
                            "error_type": type(exc).__name__,
                        }
                    )
                    + "\n",
                )
            except (OSError, ValueError):
                pass
            raise
