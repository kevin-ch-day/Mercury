"""Bounded read-only ordered row/DDL fingerprints for isolated recovery comparison."""

from __future__ import annotations

import base64
import hashlib
import json

from mercury.database.mariadb.identifiers import quote_ident


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
    config, database: str, *, max_rows: int = 1_000_000, max_bytes: int = 2 * 1024**3
) -> dict:
    import pymysql

    schema = quote_ident(database, what="database")
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
                "SELECT TABLE_NAME,TABLE_TYPE,ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
                (database,),
            )
            objects = c.fetchall()
        for name, kind, engine in objects:
            if kind != "VIEW" and (kind != "BASE TABLE" or engine != "InnoDB"):
                raise ValueError(
                    f"Recovery fingerprint cannot qualify {kind} {name!r} with engine {engine!r}"
                )

        for name, kind, engine in objects:
            table = f"{schema}.{quote_ident(name, what='table or view')}"
            if kind == "VIEW":
                with connection.cursor() as c:
                    c.execute(f"SHOW CREATE VIEW {table}")
                    definition = c.fetchone()[1:]
                result.setdefault("views", {})[name] = {
                    "ddl_sha256": hashlib.sha256(encode_row(definition)).hexdigest()
                }
                continue
            with connection.cursor() as c:
                c.execute(f"SHOW CREATE TABLE {table}")
                ddl = c.fetchone()[1]
                c.execute(f"SHOW INDEX FROM {table} WHERE Key_name='PRIMARY'")
                primary = [x[4] for x in sorted(c.fetchall(), key=lambda x: x[3])]
            h = hashlib.sha256()
            count = 0
            # Store only fixed-size digests for tables without a primary key.
            # Sorting preserves duplicates and makes the physical row order irrelevant.
            unordered_digests: list[bytes] = []
            order_by = ""
            if primary:
                columns = ", ".join(quote_ident(key, what="primary key column") for key in primary)
                order_by = f" ORDER BY {columns}"
            with connection.cursor(pymysql.cursors.SSCursor) as c:
                c.execute(
                    f"SELECT * FROM {table}{order_by} LIMIT %s",
                    (max_rows - total_rows + 1,),
                )
                for row in c:
                    encoded = encode_row(row)
                    count += 1
                    total_rows += 1
                    total_bytes += len(encoded)
                    if total_rows > max_rows or total_bytes > max_bytes:
                        raise ValueError(
                            f"Recovery fingerprint exceeds row or byte budget at table {name!r}"
                        )
                    if primary:
                        h.update(len(encoded).to_bytes(8, "big"))
                        h.update(encoded)
                    else:
                        unordered_digests.append(hashlib.sha256(encoded).digest())
            table_fingerprint = {
                "rows": count,
                "ddl_sha256": hashlib.sha256(ddl.encode()).hexdigest(),
                "engine": engine,
                "primary_key": primary,
            }
            if not primary:
                unordered_digests.sort()
                for digest in unordered_digests:
                    h.update(digest)
                table_fingerprint["row_order"] = "sorted_typed_row_sha256"
                result["scope"] = (
                    "DDL and all typed rows ordered by primary key or sorted row digest; "
                    "read-only consistent snapshot"
                )
            table_fingerprint["typed_rows_sha256"] = h.hexdigest()
            result["tables"][name] = table_fingerprint
        return result
    finally:
        connection.close()
