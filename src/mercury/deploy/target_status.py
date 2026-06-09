"""Classify target database state on the server for deployment planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.deploy.verification import verify_deployed_database

TargetStatus = Literal[
    "missing",
    "exists",
    "exists_verified",
    "exists_empty",
    "exists_unverified",
    "exists_conflict",
]


@dataclass(frozen=True)
class TargetDatabaseState:
    status: TargetStatus
    exists_on_server: bool
    detail: str
    table_count: int | None = None
    total_bytes: int | None = None


def target_status_label(status: TargetStatus) -> str:
    """Human-readable target status for plan output."""
    labels = {
        "missing": "missing",
        "exists": "exists",
        "exists_verified": "exists; verified",
        "exists_empty": "exists; empty",
        "exists_unverified": "exists; not verified",
        "exists_conflict": "exists; conflict/unknown",
    }
    return labels.get(status, status)


def classify_target_database(
    database: str,
    *,
    config: MariaDbConnectionConfig | None,
    server_databases: set[str],
    manifest_path: Path | None = None,
    row_fn=None,
) -> TargetDatabaseState:
    """Read-only classification used before any import is planned."""
    if database not in server_databases:
        return TargetDatabaseState(
            status="missing",
            exists_on_server=False,
            detail="not present on server",
        )

    if config is None:
        return TargetDatabaseState(
            status="exists_unverified",
            exists_on_server=True,
            detail="present on server; MariaDB config unavailable for inspection",
        )

    inspect = inspect_database_on_server(database, config, row_fn=row_fn)
    if inspect.error:
        return TargetDatabaseState(
            status="exists_conflict",
            exists_on_server=True,
            detail=inspect.error,
        )
    if not inspect.exists_on_server:
        return TargetDatabaseState(
            status="exists_conflict",
            exists_on_server=True,
            detail="listed on server but information_schema could not confirm",
        )

    table_count = inspect.table_count
    total_bytes = inspect.total_bytes
    if table_count == 0:
        return TargetDatabaseState(
            status="exists_empty",
            exists_on_server=True,
            detail="database exists with no tables",
            table_count=0,
            total_bytes=total_bytes,
        )

    if manifest_path is not None and manifest_path.is_file():
        post = verify_deployed_database(
            database,
            manifest_path=manifest_path,
            config=config,
            row_fn=row_fn,
        )
        if post.verified:
            return TargetDatabaseState(
                status="exists_verified",
                exists_on_server=True,
                detail="appears healthy/verified",
                table_count=table_count,
                total_bytes=total_bytes,
            )
        return TargetDatabaseState(
            status="exists_unverified",
            exists_on_server=True,
            detail=post.detail or "has tables; verification not confirmed",
            table_count=table_count,
            total_bytes=total_bytes,
        )

    return TargetDatabaseState(
        status="exists_verified",
        exists_on_server=True,
        detail="basic verified (has tables)",
        table_count=table_count,
        total_bytes=total_bytes,
    )
