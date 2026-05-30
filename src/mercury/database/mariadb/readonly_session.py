"""Batch read-only SQL execution helpers for MariaDB client and pymysql."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from mercury.database.mariadb.client import run_client_query, run_client_script
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.session import (
    _filter_user_databases,
    _pymysql_fetch_scalar,
    _pymysql_fetch_scalars,
    connect_mariadb,
)


@contextmanager
def readonly_connection(config: MariaDbConnectionConfig) -> Iterator:
    """
    Reuse one connection/session for multiple read-only queries.

    pymysql: single TCP/socket connection.
    client: batches via multi-statement script where possible.
    """
    if config.use_client:
        yield _ClientReadonlySession(config)
    else:
        connection = connect_mariadb(config)
        try:
            yield _PymysqlReadonlySession(connection)
        finally:
            connection.close()


class _PymysqlReadonlySession:
    def __init__(self, connection) -> None:
        self._connection = connection

    def scalar(self, sql: str) -> str:
        return _pymysql_fetch_scalar(self._connection, sql)

    def scalars(self, sql: str) -> list[str]:
        return _pymysql_fetch_scalars(self._connection, sql)

    def row(self, sql: str) -> list[str]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql)
            fetched = cursor.fetchone()
        if not fetched:
            return []
        return [str(value) for value in fetched]

    def rows(self, sql: str) -> list[list[str]]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql)
            fetched = cursor.fetchall()
        return [[str(value) for value in row] for row in fetched]


class _ClientReadonlySession:
    def __init__(self, config: MariaDbConnectionConfig) -> None:
        self._config = config

    def scalar(self, sql: str) -> str:
        values = self.scalars(sql)
        return values[0] if values else ""

    def scalars(self, sql: str) -> list[str]:
        raw = run_client_query(self._config, sql)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) <= 1:
            return lines
        return [line.split("\t")[0] for line in lines]

    def row(self, sql: str) -> list[str]:
        raw = run_client_query(self._config, sql).strip()
        if not raw:
            return []
        return raw.split("\t")

    def rows(self, sql: str) -> list[list[str]]:
        raw = run_client_query(self._config, sql)
        rows: list[list[str]] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(stripped.split("\t"))
        return rows

    def script_rows(self, sql_script: str) -> list[list[str]]:
        """Run a multi-statement script; return non-empty result rows."""
        raw = run_client_script(self._config, sql_script)
        rows: list[list[str]] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(stripped.split("\t"))
        return rows


def probe_server_facts(config: MariaDbConnectionConfig) -> dict[str, object]:
    """
    Fetch ping, version, current user, and database list using minimal round-trips.

    client mode: one mariadb subprocess with a multi-statement script.
    pymysql: one connection, four queries.
    """
    with readonly_connection(config) as session:
        if config.use_client and isinstance(session, _ClientReadonlySession):
            rows = session.script_rows(
                "\n".join(
                    [
                        "SELECT 1;",
                        "SELECT VERSION();",
                        "SELECT CURRENT_USER();",
                        "SHOW DATABASES;",
                    ]
                )
            )
            if len(rows) < 4:
                raise ValueError("Unexpected probe script output")
            ping_ok = rows[0][0] if rows[0] else ""
            version = rows[1][0] if len(rows) > 1 and rows[1] else ""
            current_user = rows[2][0] if len(rows) > 2 and rows[2] else ""
            database_names = [row[0] for row in rows[3:] if row]
            return {
                "ping_ok": ping_ok,
                "version": version,
                "current_user": current_user,
                "database_names": database_names,
                "sql_executed": [
                    "SELECT 1 AS ok",
                    "SELECT VERSION() AS version",
                    "SELECT CURRENT_USER() AS mercury_current_user",
                    "SHOW DATABASES",
                ],
            }

        ping_sql = "SELECT 1 AS ok"
        version_sql = "SELECT VERSION() AS version"
        user_sql = "SELECT CURRENT_USER() AS mercury_current_user"
        show_sql = "SHOW DATABASES"
        return {
            "ping_ok": session.scalar(ping_sql),
            "version": session.scalar(version_sql),
            "current_user": session.scalar(user_sql),
            "database_names": session.scalars(show_sql),
            "sql_executed": [ping_sql, version_sql, user_sql, show_sql],
        }


def fetch_user_database_names_session(config: MariaDbConnectionConfig) -> list[str]:
    """SHOW DATABASES via a single connection/session."""
    with readonly_connection(config) as session:
        names = session.scalars("SHOW DATABASES")
    return _filter_user_databases(names)
