"""Read-only MariaDB access via mariadb/mysql CLI (Fedora socket auth)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from mercury.database.mariadb.config import (
    MariaDbConfigError,
    MariaDbConnectionConfig,
    assert_connection_tls,
)
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.readonly_sql import assert_live_readonly_sql

CLIENT_TOOLS = ("mariadb", "mysql")


def select_client_tool() -> str:
    for name in CLIENT_TOOLS:
        if shutil.which(name):
            return name
    raise MariaDbLiveError(
        "No mariadb or mysql client found on PATH for read-only CLI access."
    )


def build_client_argv(config: MariaDbConnectionConfig, sql: str) -> list[str]:
    argv = build_script_argv(config)
    argv.extend(["-e", sql])
    return argv


def build_script_argv(
    config: MariaDbConnectionConfig, *extra_flags: str
) -> list[str]:
    """mariadb/mysql argv for stdin SQL (no ``-e``)."""
    try:
        assert_connection_tls(config)
    except MariaDbConfigError as exc:
        raise MariaDbLiveError(str(exc)) from exc
    tool = select_client_tool()
    argv = [tool, "-N", "-B", *extra_flags, "-u", config.user]
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
    assert_live_readonly_sql(sql)
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
    assert_live_readonly_sql(sql_script)
    if runner is not None:
        return runner(config, sql_script)

    argv = build_script_argv(config)
    return _run_client_argv(config, argv, input_text=sql_script)


def run_client_force_script(
    config: MariaDbConnectionConfig,
    sql_script: str,
    *,
    timeout: int | None = None,
) -> tuple[int, str, str]:
    """Run a read-only script with ``--force``; do not raise on statement errors.

    mariadb --force can exit 0 even when some statements fail, so callers must
    inspect stderr. Used by dumpability SHOW CREATE probes.
    """
    assert_live_readonly_sql(sql_script)
    argv = build_script_argv(config, "--force")
    wait = max(int(timeout or config.connect_timeout or 10), 60)
    try:
        with client_process_credentials(config) as (env, extra):
            result = subprocess.run(
                prepend_client_defaults(argv, extra),
                input=sql_script,
                capture_output=True,
                text=True,
                env=env,
                timeout=wait,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise MariaDbLiveError(
            f"MariaDB CLI dumpability probe timed out after {wait}s"
        ) from exc
    return result.returncode, result.stdout or "", result.stderr or ""


def _cnf_escape_password(password: str) -> str:
    if "\n" in password or "\r" in password:
        raise MariaDbLiveError("MariaDB password must not contain newlines.")
    return password.replace("\\", "\\\\").replace('"', '\\"')


def prepend_client_defaults(argv: list[str], extra: list[str]) -> list[str]:
    """Insert ``--defaults-extra-file`` as the first option after the tool name."""
    if not extra:
        return argv
    if not argv:
        return list(extra)
    return [argv[0], *extra, *argv[1:]]


@contextmanager
def client_process_credentials(
    config: MariaDbConnectionConfig,
) -> Iterator[tuple[dict[str, str], list[str]]]:
    """Yield subprocess env/argv extras without putting the password in MYSQL_PWD."""
    env = os.environ.copy()
    env.pop("MYSQL_PWD", None)
    if not config.password:
        yield env, []
        return
    fd, path = tempfile.mkstemp(prefix="mercury-my-", suffix=".cnf")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        payload = f'[client]\npassword="{_cnf_escape_password(config.password)}"\n'.encode()
        os.write(fd, payload)
        os.close(fd)
        fd = -1
        yield env, [f"--defaults-extra-file={path}"]
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(path)
        except OSError:
            pass


def _run_client_argv(
    config: MariaDbConnectionConfig,
    argv: list[str],
    *,
    input_text: str | None = None,
) -> str:
    try:
        with client_process_credentials(config) as (env, extra):
            result = subprocess.run(
                prepend_client_defaults(argv, extra),
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
