"""Planned mariadb-dump commands (not executed in seed mode)."""

from typing import Literal

from pydantic import BaseModel

from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

DumpKind = Literal["full", "schema_only"]

DUMP_TOOLS = ("mariadb-dump", "mysqldump")


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


def _connection_prefix(
    *,
    user: str,
    host: str,
    port: int,
    unix_socket: str | None,
    tool: str = "mariadb-dump",
) -> str:
    if unix_socket:
        return f"{tool} -u {user} --socket={unix_socket} --protocol=SOCKET"
    return f"{tool} --skip-ssl -h {host} -P {port} -u {user}"


def build_planned_dump_command(
    database: str,
    kind: DumpKind,
    *,
    host: str = "localhost",
    port: int = 3306,
    user: str = "USER",
    unix_socket: str | None = None,
    tool: str = "mariadb-dump",
) -> str:
    """MariaDB logical dump command (plan/display only)."""
    base = _connection_prefix(
        user=user, host=host, port=port, unix_socket=unix_socket, tool=tool
    )
    if kind == BACKUP_KIND_SCHEMA_ONLY:
        return (
            f"{base} --no-data --routines --triggers --events "
            f"--databases {database}  # schema-only"
        )
    return f"{base} --single-transaction --databases {database}  # full logical"


def select_dump_tool(tools_on_path: dict[str, str] | None = None) -> str:
    """Prefer mariadb-dump, then mysqldump."""
    if tools_on_path is None:
        from mercury.database.mariadb.probe import probe_client_tooling

        tools_on_path = probe_client_tooling().tools
    for name in DUMP_TOOLS:
        path = tools_on_path.get(name, "not found")
        if path != "not found":
            return name
    raise RuntimeError(
        "No mariadb-dump or mysqldump found on PATH. "
        f"Expected one of: {', '.join(DUMP_TOOLS)}"
    )


def build_dump_argv(
    database: str,
    kind: DumpKind,
    *,
    host: str = "localhost",
    port: int = 3306,
    user: str = "USER",
    tool: str = "mariadb-dump",
    unix_socket: str | None = None,
    ssl_disabled: bool = True,
) -> list[str]:
    """Build argv for subprocess (password supplied via MYSQL_PWD env)."""
    argv = [tool, "-u", user]
    if unix_socket:
        argv[1:1] = [f"--socket={unix_socket}", "--protocol=SOCKET"]
    else:
        argv[1:1] = ["-h", host, "-P", str(port)]
        if ssl_disabled:
            argv[1:1] = ["--skip-ssl"]
    if kind == BACKUP_KIND_SCHEMA_ONLY:
        argv.extend(["--no-data", "--routines", "--triggers", "--events"])
    else:
        argv.extend(["--single-transaction"])
    argv.extend(["--databases", database])
    return argv


def build_dump_argv_for_config(
    database: str,
    kind: DumpKind,
    config,
    *,
    tool: str = "mariadb-dump",
) -> list[str]:
    """Build dump argv from MariaDbConnectionConfig (supports unix socket)."""
    return build_dump_argv(
        database,
        kind,
        host=config.host,
        port=config.port,
        user=config.user,
        tool=tool,
        unix_socket=config.unix_socket,
        ssl_disabled=config.ssl_disabled,
    )


def build_planned_dump(
    database: str,
    kind: DumpKind,
    *,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    unix_socket: str | None = None,
    tool: str = "mariadb-dump",
) -> PlannedDump:
    h = host or "localhost"
    p = port or 3306
    u = user or "USER"
    cmd = build_planned_dump_command(
        database, kind, host=h, port=p, user=u, unix_socket=unix_socket, tool=tool
    )
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
        tool=tool,
    )
