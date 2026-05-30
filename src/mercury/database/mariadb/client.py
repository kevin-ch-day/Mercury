"""Read-only MariaDB access via mariadb/mysql CLI (Fedora socket auth)."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Literal

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.errors import MariaDbLiveError

CLIENT_TOOLS = ("mariadb", "mysql")


def select_client_tool() -> str:
    for name in CLIENT_TOOLS:
        if shutil.which(name):
            return name
    raise MariaDbLiveError(
        "No mariadb or mysql client found on PATH for read-only CLI access."
    )


def build_client_argv(config: MariaDbConnectionConfig, sql: str) -> list[str]:
    tool = select_client_tool()
    argv = [tool, "-N", "-B", "-u", config.user, "-e", sql]
    if config.unix_socket:
        argv[1:1] = [f"--socket={config.unix_socket}", "--protocol=SOCKET"]
    else:
        argv[1:1] = ["-h", config.host, "-P", str(config.port)]
        if config.ssl_disabled:
            argv[1:1] = ["--skip-ssl"]
    return argv


def run_client_query(
    config: MariaDbConnectionConfig,
    sql: str,
    *,
    runner=None,
) -> str:
    """Run a single read-only SQL statement via mariadb/mysql CLI."""
    if runner is not None:
        return runner(config, sql)

    argv = build_client_argv(config, sql)
    return _run_client_argv(config, argv)


def run_client_script(
    config: MariaDbConnectionConfig,
    sql_script: str,
    *,
    runner=None,
) -> str:
    """
    Run multiple read-only SQL statements in one mariadb/mysql process.

    Uses stdin to avoid spawning a subprocess per query (faster on Fedora socket auth).
    """
    if runner is not None:
        return runner(config, sql_script)

    tool = select_client_tool()
    argv = [tool, "-N", "-B", "-u", config.user]
    if config.unix_socket:
        argv[1:1] = [f"--socket={config.unix_socket}", "--protocol=SOCKET"]
    else:
        argv[1:1] = ["-h", config.host, "-P", str(config.port)]
        if config.ssl_disabled:
            argv[1:1] = ["--skip-ssl"]
    return _run_client_argv(config, argv, input_text=sql_script)


def _run_client_argv(
    config: MariaDbConnectionConfig,
    argv: list[str],
    *,
    input_text: str | None = None,
) -> str:
    env = os.environ.copy()
    if config.password:
        env["MYSQL_PWD"] = config.password

    try:
        result = subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            env=env,
            timeout=max(config.connect_timeout, 30),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MariaDbLiveError(
            f"MariaDB CLI query timed out after {config.connect_timeout}s"
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        target = config.unix_socket or f"{config.host}:{config.port}"
        raise MariaDbLiveError(
            f"MariaDB CLI query failed ({target}): {detail or 'unknown error'}"
        )
    return result.stdout


def run_client_sql(config: MariaDbConnectionConfig, sql: str) -> None:
    """Execute one SQL statement via mariadb/mysql CLI (DDL/DML)."""
    argv = build_client_argv(config, sql)
    _run_client_argv(config, argv)


def client_fetch_scalars(config: MariaDbConnectionConfig, sql: str) -> list[str]:
    raw = run_client_query(config, sql)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) <= 1:
        return lines
    # Batch tab-separated output may be single column across lines
    values: list[str] = []
    for line in lines:
        parts = line.split("\t")
        values.append(parts[0])
    return values


def client_fetch_scalar(config: MariaDbConnectionConfig, sql: str) -> str:
    values = client_fetch_scalars(config, sql)
    return values[0] if values else ""


def connection_label(config: MariaDbConnectionConfig) -> str:
    if config.unix_socket:
        return f"connected (socket:{config.unix_socket})"
    return f"connected ({config.host}:{config.port})"


def access_mode(config: MariaDbConnectionConfig) -> Literal["client", "pymysql"]:
    return "client" if config.use_client else "pymysql"
