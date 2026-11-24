"""Safety rules for deploying backup artifacts onto a fresh MariaDB host."""

from __future__ import annotations

import shlex

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.core import DatabaseRole, classify_database
from mercury.database.core.scope import is_active_dev_target
from mercury.database.mariadb.identifiers import quote_ident
from mercury.deploy.models import DeployOptions
from mercury.core.safety import LIVE_RESTORE_PROHIBITED_DATABASES

# Backup eligibility is deliberately broader than fresh-host deployment authority.
# Noctivar and the provider-secret catalog remain backup-only.
DEPLOYMENT_SOURCE_DATABASES = frozenset({
    "android_permission_intel",
    "erebus_threat_intel_prod",
    "scytaledroid_core_prod",
    "obsidiandroid_core_prod",
})


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
    if database in DEPLOYMENT_SOURCE_DATABASES:
        return
    if allow_development_deploy and is_active_dev_target(database):
        return
    raise BackupExecutionError(
        f"Refusing deployment target '{database}': no deployment authorization "
        "(backup eligibility does not authorize deployment; backup-only catalogs are prohibited)."
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
    if target_database in LIVE_RESTORE_PROHIBITED_DATABASES:
        return [], "Backup-only catalog: live deployment is prohibited."
    quoted_dump = shlex.quote(dump_path)
    commands: list[str] = []

    if exists_on_server:
        if options.skip_existing and not (options.allow_overwrite_database or options.allow_drop_database):
            return [], f"Target database '{target_database}' already exists (skip-existing enabled)."
        return [], "Existing database replacement is disabled for deployment."

    if options.allow_overwrite_database or options.allow_drop_database:
        return [], "Overwrite/drop modes are disabled for deployment."

    if options.allow_create_database:
        # Failure on an existing target is essential: a plan is only a snapshot.
        commands.append(f"CREATE DATABASE {quoted}")
    else:
        return [], "Target database missing and allow_create_database is disabled."

    if dump_path.endswith(".gz"):
        commands.append(f"gunzip -c {quoted_dump} | mariadb {target_database}")
    else:
        commands.append(f"mariadb {target_database} < {quoted_dump}")

    return commands, None
