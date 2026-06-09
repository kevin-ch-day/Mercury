"""Safety rules for deploying backup artifacts onto a fresh MariaDB host."""

from __future__ import annotations

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.core import DatabaseRole, classify_database
from mercury.deploy.models import DeployOptions


def assert_deployment_target(database: str) -> None:
    """Only approved backup-source database names may be deployment targets."""
    classification = classify_database(database)
    if classification.role == DatabaseRole.RESTORE_CHECK_TEMP:
        raise BackupExecutionError(
            f"Refusing deployment target '{database}': _restorecheck_* names are temporary only."
        )
    if not classification.backup_source:
        raise BackupExecutionError(
            f"Refusing deployment target '{database}': not an approved backup-source database."
        )


def planned_import_commands(
    *,
    target_database: str,
    dump_path: str,
    options: DeployOptions,
    exists_on_server: bool,
) -> tuple[list[str], str | None]:
    """Return planned shell/SQL steps and optional skip reason."""
    commands: list[str] = []

    if exists_on_server:
        if options.skip_existing and not options.allow_overwrite_database:
            return [], f"Target database '{target_database}' already exists (skip-existing enabled)."
        if options.allow_overwrite_database:
            if options.allow_drop_database:
                commands.append(f"DROP DATABASE IF EXISTS `{target_database}`")
            else:
                return [], (
                    f"Target database '{target_database}' exists; overwrite requires allow_drop_database."
                )

    if options.allow_create_database:
        if exists_on_server and options.allow_overwrite_database and options.allow_drop_database:
            commands.append(f"CREATE DATABASE `{target_database}`")
        else:
            commands.append(f"CREATE DATABASE IF NOT EXISTS `{target_database}`")
    elif not exists_on_server:
        return [], "Target database missing and allow_create_database is disabled."

    if dump_path.endswith(".gz"):
        commands.append(f"gunzip -c {dump_path} | mariadb {target_database}")
    else:
        commands.append(f"mariadb {target_database} < {dump_path}")

    return commands, None
