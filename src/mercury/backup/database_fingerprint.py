"""Bounded read-only ordered row/DDL fingerprints for isolated recovery comparison."""

from __future__ import annotations

import base64
import hashlib
import json
import re


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError("Unsupported database identifier")
    return "`" + value + "`"


def encode_row(row) -> bytes:
    def value(x):
        if x is None:
            return ["null"]
        if isinstance(x, bytes):
            return ["bytes", base64.b64encode(x).decode("ascii")]
        return [type(x).__name__, str(x)]

    return json.dumps(
        [value(x) for x in row], ensure_ascii=True, separators=(",", ":")
    ).encode()


def fingerprint(
    config, database: str, *, max_rows=1_000_000, max_bytes=2 * 1024**3
) -> dict:
    import pymysql

    schema = _identifier(database)
    connection = pymysql.connect(
        unix_socket=config.unix_socket,
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=database,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        autocommit=False,
    )
    result = {
        "schema": database,
        "tables": {},
        "scope": "DDL and all typed rows ordered by primary key; read-only consistent snapshot",
    }
    total_rows = total_bytes = 0
    try:
        with connection.cursor() as c:
            c.execute("SET SESSION time_zone='+00:00', max_statement_time=30")
            c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            c.execute("SELECT VERSION()")
            result["server_version"] = c.fetchone()[0]
            c.execute(
                "SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
                (database,),
            )
            tables = c.fetchall()
        for name, engine in tables:
            if engine != "InnoDB":
                raise ValueError(
                    "Recovery fingerprint requires InnoDB base tables only"
                )
            table = _identifier(name)
            with connection.cursor() as c:
                c.execute(f"SHOW CREATE TABLE {schema}.{table}")
                ddl = c.fetchone()[1]
                c.execute(f"SHOW INDEX FROM {schema}.{table} WHERE Key_name='PRIMARY'")
                primary = [x[4] for x in sorted(c.fetchall(), key=lambda x: x[3])]
                if not primary:
                    raise ValueError(
                        "Recovery fingerprint requires a deterministic primary key"
                    )
            h = hashlib.sha256()
            count = 0
            with connection.cursor(pymysql.cursors.SSCursor) as c:
                c.execute(
                    f"SELECT * FROM {schema}.{table} ORDER BY "
                    + ",".join(_identifier(x) for x in primary)
                )
                for row in c:
                    encoded = encode_row(row)
                    h.update(len(encoded).to_bytes(8, "big"))
                    h.update(encoded)
                    count += 1
                    total_rows += 1
                    total_bytes += len(encoded)
                    if total_rows > max_rows or total_bytes > max_bytes:
                        raise ValueError(
                            "Recovery fingerprint exceeds bounded inventory"
                        )
            result["tables"][name] = {
                "rows": count,
                "typed_rows_sha256": h.hexdigest(),
                "ddl_sha256": hashlib.sha256(ddl.encode()).hexdigest(),
                "engine": engine,
                "primary_key": primary,
            }
        return result
    finally:
        connection.rollback()
        connection.close()
