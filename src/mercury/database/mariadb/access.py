"""Compare platform catalog databases to live server presence."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.database.core import backup_source_names, classify_database
from mercury.database.core.catalog import PLATFORM_CATALOG
from mercury.database.discovery import discover
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.session import MariaDbLiveError, fetch_user_database_names


class PlatformAccessRecord(BaseModel):
    name: str
    project: str | None = None
    role: str
    backup_source: bool
    on_server: bool
    status: str


class PlatformAccessReport(BaseModel):
    connection: str
    access_mode: str
    server_database_count: int
    records: list[PlatformAccessRecord] = Field(default_factory=list)
    present: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    unexpected_on_server: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def build_platform_access_report(
    config: MariaDbConnectionConfig | None = None,
    *,
    names_fn=None,
) -> PlatformAccessReport:
    """
    Check which platform/catalog databases exist on the live server.

    Read-only: SHOW DATABASES only for server side.
    """
    fetch_names = names_fn or fetch_user_database_names
    inventory = discover("live", mariadb_config=config)
    cfg = config
    if cfg is None:
        from mercury.database.mariadb.config import load_mariadb_config

        cfg = load_mariadb_config()

    try:
        server_names = set(fetch_names(cfg))
    except MariaDbLiveError as exc:
        raise exc

    catalog_names = {entry.name for entry in PLATFORM_CATALOG}
    records: list[PlatformAccessRecord] = []

    for entry in PLATFORM_CATALOG:
        classification = classify_database(entry.name)
        on_server = entry.name in server_names
        if on_server:
            status = "present"
        elif classification.manual_review:
            status = "unknown (not on server)"
        elif classification.dev_target:
            status = "missing dev target"
        elif classification.backup_source:
            status = "MISSING backup source"
        else:
            status = "not on server"

        records.append(
            PlatformAccessRecord(
                name=entry.name,
                project=entry.project,
                role=classification.role.value,
                backup_source=classification.backup_source,
                on_server=on_server,
                status=status,
            )
        )

    present = [r.name for r in records if r.on_server]
    missing = [r.name for r in records if not r.on_server]
    unexpected = sorted(server_names - catalog_names)

    notes = [
        "Read-only check: SHOW DATABASES compared to platform catalog.",
        "Missing prod/shared authority databases should be investigated.",
    ]
    if unexpected:
        notes.append(
            f"{len(unexpected)} non-catalog database(s) on server require manual review."
        )

    return PlatformAccessReport(
        connection=inventory.connection,
        access_mode="client" if cfg.use_client else "pymysql",
        server_database_count=len(server_names),
        records=records,
        present=present,
        missing=missing,
        unexpected_on_server=unexpected,
        notes=notes,
    )
