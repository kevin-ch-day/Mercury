"""Read-only MariaDB connection and server probe."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from mercury.database.mariadb.client import (
    access_mode,
    client_fetch_scalar,
    client_fetch_scalars,
)
from mercury.database.mariadb.config import (
    MariaDbConfigError,
    MariaDbConnectionConfig,
    load_mariadb_config,
)
from mercury.database.mariadb.errors import MariaDbDriverMissingError, MariaDbLiveError
from mercury.core.paths import LOCAL_CONFIG

SYSTEM_DATABASES = frozenset(
    {
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
    }
)

ProbeFn = Callable[[MariaDbConnectionConfig], "MariaDbServerProbe"]
NamesFn = Callable[[MariaDbConnectionConfig], list[str]]


class MariaDbServerProbe(BaseModel):
    """Result of a read-only connectivity check (no writes)."""

    host: str
    port: int
    configured_user: str
    connected: bool = False
    latency_ms: float | None = None
    server_version: str | None = None
    current_user: str | None = None
    user_database_count: int | None = None
    sample_databases: list[str] = Field(default_factory=list)
    read_only: bool = True
    driver: str = "pymysql"
    unix_socket: str | None = None
    config_path: str | None = None
    sql_executed: list[str] = Field(default_factory=list)
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


def _import_pymysql():
    try:
        import pymysql
    except ImportError as exc:
        raise MariaDbDriverMissingError(
            "pymysql is required for pymysql access mode. Install with: "
            'pip install -e ".[mariadb]" or set use_client = true in config/local.toml.'
        ) from exc
    return pymysql


def try_load_mariadb_config(path: Path | None = None) -> MariaDbConnectionConfig | None:
    """Load MariaDB config when present; return None if not configured."""
    try:
        return load_mariadb_config(path)
    except MariaDbConfigError:
        return None


def _connect_kwargs(config: MariaDbConnectionConfig) -> dict:
    kwargs: dict = {
        "user": config.user,
        "password": config.password,
        "connect_timeout": config.connect_timeout,
        "read_timeout": 30,
        "charset": "utf8mb4",
    }
    if config.unix_socket:
        kwargs["unix_socket"] = config.unix_socket
    else:
        kwargs["host"] = config.host
        kwargs["port"] = config.port
    if config.ssl_disabled:
        kwargs["ssl"] = None
    return kwargs


def connect_mariadb(config: MariaDbConnectionConfig):
    """Open a pymysql connection (caller must close)."""
    if config.use_client:
        raise MariaDbLiveError(
            "connect_mariadb() is unavailable when use_client=true. "
            "Use readonly_scalar() or readonly_scalars()."
        )
    pymysql = _import_pymysql()
    try:
        return pymysql.connect(**_connect_kwargs(config))
    except pymysql.Error as exc:
        target = config.unix_socket or f"{config.host}:{config.port}"
        raise MariaDbLiveError(f"Could not connect to MariaDB at {target}: {exc}") from exc


def _pymysql_fetch_scalar(connection, sql: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    if not row:
        return ""
    return str(row[0] if isinstance(row, (tuple, list)) else row)


def _pymysql_fetch_scalars(connection, sql: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    values: list[str] = []
    for row in rows:
        if not row:
            continue
        values.append(str(row[0] if isinstance(row, (tuple, list)) else row))
    return values


def readonly_scalar(config: MariaDbConnectionConfig, sql: str) -> str:
    """Run a read-only scalar query via configured access mode."""
    if config.use_client:
        return client_fetch_scalar(config, sql)
    connection = connect_mariadb(config)
    try:
        return _pymysql_fetch_scalar(connection, sql)
    finally:
        connection.close()


def readonly_scalars(config: MariaDbConnectionConfig, sql: str) -> list[str]:
    """Run a read-only multi-row single-column query."""
    if config.use_client:
        return client_fetch_scalars(config, sql)
    connection = connect_mariadb(config)
    try:
        return _pymysql_fetch_scalars(connection, sql)
    finally:
        connection.close()


def readonly_row(config: MariaDbConnectionConfig, sql: str) -> list[str]:
    """Run a read-only query and return the first row as string columns."""
    if config.use_client:
        from mercury.database.mariadb.client import run_client_query

        raw = run_client_query(config, sql).strip()
        if not raw:
            return []
        return raw.split("\t")
    connection = connect_mariadb(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone()
        if not row:
            return []
        return [str(value) for value in row]
    finally:
        connection.close()


def fetch_user_database_names(
    config: MariaDbConnectionConfig,
    *,
    connect_fn: NamesFn | None = None,
) -> list[str]:
    """Run SHOW DATABASES and return user databases (read-only)."""
    if connect_fn is not None:
        return _filter_user_databases(connect_fn(config))

    from mercury.database.mariadb.readonly_session import fetch_user_database_names_session

    return fetch_user_database_names_session(config)


def _filter_user_databases(names: list[str]) -> list[str]:
    return sorted(n for n in names if n.lower() not in SYSTEM_DATABASES)


def probe_mariadb_server(
    config: MariaDbConnectionConfig | None = None,
    *,
    config_path: Path | None = None,
    probe_fn: ProbeFn | None = None,
    include_database_sample: bool = True,
    sample_limit: int = 5,
) -> MariaDbServerProbe:
    """
    Read-only server probe: connect, SELECT 1, VERSION(), CURRENT_USER(), SHOW DATABASES.

    No CREATE/DROP/ALTER/INSERT/UPDATE/DELETE. Safe to run before backups are enabled.
    """
    path = config_path or LOCAL_CONFIG
    cfg = config
    if cfg is None:
        cfg = load_mariadb_config(path)

    driver = access_mode(cfg)
    base = MariaDbServerProbe(
        host=cfg.host,
        port=cfg.port,
        configured_user=cfg.user,
        driver=driver,
        unix_socket=cfg.unix_socket,
        config_path=str(path.name) if path.exists() else None,
        notes=[
            "Read-only probe only; no schema or data changes.",
            "Use mercury db discover for full inventory classification.",
        ],
    )

    if probe_fn is not None:
        return probe_fn(cfg)

    started = time.perf_counter()
    from mercury.database.mariadb.readonly_session import probe_server_facts

    facts = probe_server_facts(cfg)
    executed = list(facts["sql_executed"])
    user_databases = _filter_user_databases(list(facts["database_names"]))
    sample = user_databases[:sample_limit] if include_database_sample else []
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    return base.model_copy(
        update={
            "connected": True,
            "latency_ms": latency_ms,
            "server_version": str(facts["version"]) or None,
            "current_user": str(facts["current_user"]) or None,
            "user_database_count": len(user_databases),
            "sample_databases": sample,
            "sql_executed": executed,
        }
    )


def resolve_mariadb_target(
    config: MariaDbConnectionConfig | None = None,
) -> tuple[str, int, str]:
    """Return (host, port, user) from config or placeholders when offline."""
    cfg = config or try_load_mariadb_config()
    if cfg is None:
        return "localhost", 3306, "USER"
    return cfg.host, cfg.port, cfg.user
