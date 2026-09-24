"""Drop temporary _restorecheck_* databases after restore tests."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.core import DatabaseRole, classify_database
from mercury.database.mariadb.client import run_client_sql
from mercury.database.mariadb.config import (
    MariaDbConnectionConfig,
    load_mariadb_restore_config,
)
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.identifiers import quote_ident
from mercury.database.mariadb.session import try_load_mariadb_restore_config

# Phase 3B retained rehearsal copies. Generic cleanup must not drop them;
# use mercury restore-check retire-phase3b-restorecheck.
PHASE3B_RETAINED_RESTORECHECK = frozenset({
    "_restorecheck_erebus_threat_intel_prod_20260722T055400Z_phase3b",
    "_restorecheck_android_permission_intel_20260722T055400Z_phase3b",
})


class RestoreCheckCleanupResult(BaseModel):
    database: str
    dry_run: bool = True
    dropped: bool = False
    refused: bool = False
    message: str = ""


class RestoreCheckCleanupBatch(BaseModel):
    mode: str = "dry-run"
    databases: list[str] = Field(default_factory=list)
    results: list[RestoreCheckCleanupResult] = Field(default_factory=list)

    @property
    def dropped_count(self) -> int:
        return sum(1 for result in self.results if result.dropped)


def is_restorecheck_database(name: str) -> bool:
    return classify_database(name).role == DatabaseRole.RESTORE_CHECK_TEMP


def assert_restorecheck_database(name: str) -> None:
    if not is_restorecheck_database(name):
        raise BackupExecutionError(
            f"Refusing to drop '{name}': only _restorecheck_* temp databases are allowed."
        )


def list_restorecheck_databases(names: list[str]) -> list[str]:
    return sorted(name for name in names if is_restorecheck_database(name))


def drop_restorecheck_database(
    database: str,
    *,
    execute: bool = False,
    config: MariaDbConnectionConfig | None = None,
    allow_phase3b: bool = False,
    if_exists: bool = True,
) -> RestoreCheckCleanupResult:
    """Plan or run DROP DATABASE for a single _restorecheck_* database."""
    assert_restorecheck_database(database)
    try:
        quoted = quote_ident(database, what="restore-check database")
    except ValueError as exc:
        raise BackupExecutionError(str(exc)) from exc
    if database in PHASE3B_RETAINED_RESTORECHECK and not allow_phase3b:
        return RestoreCheckCleanupResult(
            database=database,
            dry_run=not execute,
            refused=True,
            message=(
                f"Refusing to drop retained Phase 3B rehearsal {database}. "
                "Use: mercury restore-check retire-phase3b-restorecheck"
            ),
        )
    command = (
        f"DROP DATABASE IF EXISTS {quoted}"
        if if_exists
        else f"DROP DATABASE {quoted}"
    )

    if not execute:
        return RestoreCheckCleanupResult(
            database=database,
            dry_run=True,
            message=f"Would drop restore-check database {database} with {command}.",
        )

    cfg = config or try_load_mariadb_restore_config()
    if cfg is None:
        cfg = load_mariadb_restore_config()

    try:
        run_client_sql(cfg, command)
    except MariaDbLiveError as exc:
        return RestoreCheckCleanupResult(
            database=database,
            refused=True,
            message=str(exc),
        )

    return RestoreCheckCleanupResult(
        database=database,
        dry_run=False,
        dropped=True,
        message=f"Dropped restore-check database {database}.",
    )


def cleanup_restorecheck_databases(
    names: list[str],
    *,
    execute: bool = False,
    config: MariaDbConnectionConfig | None = None,
) -> RestoreCheckCleanupBatch:
    """Plan or drop all _restorecheck_* databases in ``names``."""
    targets = list_restorecheck_databases(names)
    results = [
        drop_restorecheck_database(name, execute=execute, config=config)
        for name in targets
    ]
    return RestoreCheckCleanupBatch(
        mode="live" if execute else "dry-run",
        databases=targets,
        results=results,
    )


def discover_restorecheck_names() -> list[str]:
    """Return _restorecheck_* database names from live inventory."""
    from mercury.core.runtime import should_probe_database_status
    from mercury.database import discover

    if not should_probe_database_status():
        return []
    try:
        inventory = discover("live")
    except Exception:  # noqa: BLE001 - optional dashboard discovery fails closed
        return []
    return list_restorecheck_databases([entry.name for entry in inventory.entries])
