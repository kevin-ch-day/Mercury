"""Safety rules for deploying backup artifacts onto a fresh MariaDB host."""

from __future__ import annotations

import shlex

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.core import DatabaseRole, classify_database
from mercury.database.core.scope import is_active_dev_target
from mercury.database.mariadb.identifiers import quote_ident
from mercury.deploy.models import DeployOptions


def _quoted_target(database: str) -> str:
    try:
        return quote_ident(database, what="deployment target")
    except ValueError as exc:
        raise BackupExecutionError(str(exc)) from exc


def assert_deployment_target(database: str, *, allow_development_deploy: bool = False) -> None:
    """Reject unsafe deployment targets unless the explicit dev lane was selected."""
    _quoted_target(database)
    classification = classify_database(database)
    if classification.role == DatabaseRole.RESTORE_CHECK_TEMP:
        raise BackupExecutionError(
            f"Refusing deployment target '{database}': _restorecheck_* names are temporary only."
        )
    if classification.backup_source:
        return
    if allow_development_deploy and is_active_dev_target(database):
        return
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
    quoted = _quoted_target(target_database)
    quoted_dump = shlex.quote(dump_path)
    commands: list[str] = []

    if exists_on_server:
        if options.skip_existing and not options.allow_overwrite_database:
            return [], f"Target database '{target_database}' already exists (skip-existing enabled)."
        if options.allow_overwrite_database:
            if options.allow_drop_database:
                commands.append(f"DROP DATABASE IF EXISTS {quoted}")
            else:
                return [], (
                    f"Target database '{target_database}' exists; overwrite requires allow_drop_database."
                )

    if options.allow_create_database:
        if exists_on_server and options.allow_overwrite_database and options.allow_drop_database:
            commands.append(f"CREATE DATABASE {quoted}")
        else:
            commands.append(f"CREATE DATABASE IF NOT EXISTS {quoted}")
    elif not exists_on_server:
        return [], "Target database missing and allow_create_database is disabled."

    if dump_path.endswith(".gz"):
        commands.append(f"gunzip -c {quoted_dump} | mariadb {target_database}")
    else:
        commands.append(f"mariadb {target_database} < {quoted_dump}")

    return commands, None
