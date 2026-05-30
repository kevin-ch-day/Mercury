"""Planned mariadb-dump commands (not executed in seed mode)."""

from typing import Literal

from pydantic import BaseModel

from mercury.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

DumpKind = Literal["full", "schema_only"]


class PlannedDump(BaseModel):
    database: str
    backup_kind: DumpKind
    tool: str = "mariadb-dump"
    command: str
    output_file: str
    notes: str = ""


def planned_output_filename(database: str, kind: DumpKind, timestamp: str = "TIMESTAMP") -> str:
    if kind == BACKUP_KIND_SCHEMA_ONLY:
        return f"{database}_{timestamp}.schema.sql.gz"
    return f"{database}_{timestamp}.sql.gz"


def build_planned_dump_command(
    database: str,
    kind: DumpKind,
    *,
    host: str = "localhost",
    port: int = 3306,
    user: str = "USER",
) -> str:
    """MariaDB logical dump command per official mariadb-dump direction (plan only)."""
    base = f"mariadb-dump -h {host} -P {port} -u {user}"
    if kind == BACKUP_KIND_SCHEMA_ONLY:
        return (
            f"{base} --no-data --routines --triggers --events "
            f"--databases {database}  # schema-only"
        )
    return f"{base} --single-transaction --databases {database}  # full logical"


def build_planned_dump(
    database: str,
    kind: DumpKind,
    *,
    host: str | None = None,
    port: int | None = None,
) -> PlannedDump:
    h = host or "localhost"
    p = port or 3306
    cmd = build_planned_dump_command(database, kind, host=h, port=p)
    outfile = planned_output_filename(database, kind)
    notes = (
        "Schema-only: structure for review and empty shells."
        if kind == BACKUP_KIND_SCHEMA_ONLY
        else "Full logical: schema + data for DR and prod-to-dev sync."
    )
    return PlannedDump(
        database=database,
        backup_kind=kind,
        command=cmd,
        output_file=outfile,
        notes=notes,
    )
