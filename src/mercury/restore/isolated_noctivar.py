"""Rehearse Noctivar recovery on a new private Unix socket, never the live server."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from mercury.backup.database_fingerprint import fingerprint
from mercury.backup.durability import durable_replace_text
from mercury.backup.verification import verify_backup_artifacts
from mercury.database.mariadb.config import MariaDbConnectionConfig


def rehearse(backup: Path, receipt: Path) -> dict:
    verified = verify_backup_artifacts(backup, database="noctivar")
    if not verified.verified:
        raise ValueError("Recovery source artifacts did not verify")
    if receipt.exists() or receipt.is_symlink():
        raise ValueError("Recovery receipt already exists")
    manifest = json.loads((backup / "manifest.json").read_text())
    if manifest["database"] != "noctivar":
        raise ValueError("Recovery source must be Noctivar")
    expected = json.loads((backup / "SOURCE_FINGERPRINT.json").read_text())
    root = Path(tempfile.mkdtemp(prefix="mercury-noctivar-restore-", dir="/tmp"))
    root.chmod(0o700)
    socket = root / "mysql.sock"
    if root.parent != Path("/tmp") or socket == Path("/var/lib/mysql/mysql.sock"):
        raise ValueError("Isolated recovery target identity refused")
    with (root / "install.log").open("wb") as log:
        subprocess.run(
            [
                "mariadb-install-db",
                "--no-defaults",
                f"--datadir={root / 'data'}",
                "--auth-root-authentication-method=normal",
                "--skip-test-db",
            ],
            check=True,
            stdout=log,
            stderr=log,
            timeout=60,
        )
    server = subprocess.Popen(
        [
            "mariadbd",
            "--no-defaults",
            f"--datadir={root / 'data'}",
            f"--socket={socket}",
            f"--pid-file={root / 'mysql.pid'}",
            f"--log-error={root / 'server.log'}",
            "--skip-networking",
            "--local-infile=0",
            "--innodb-buffer-pool-size=128M",
            "--max-allowed-packet=1G",
            "--performance-schema=OFF",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    processes = []
    try:
        import pymysql

        deadline = time.monotonic() + 30
        while True:
            if server.poll() is not None:
                raise ValueError(
                    "Isolated MariaDB exited during startup; inspect retained server log"
                )
            try:
                connection = pymysql.connect(
                    unix_socket=str(socket), user="root", connect_timeout=1
                )
                connection.close()
                break
            except pymysql.MySQLError:
                if time.monotonic() > deadline:
                    raise ValueError("Isolated MariaDB startup timeout")
                time.sleep(0.1)
        dump = backup / manifest["dump_file"]
        if dump.parent != backup or dump.is_symlink():
            raise ValueError("Unsafe recovery dump locator")
        with (root / "import.log").open("wb") as log:
            decompress = subprocess.Popen(
                ["gzip", "-dc", "--", str(dump)], stdout=subprocess.PIPE, stderr=log
            )
            processes.append(decompress)
            client = subprocess.Popen(
                [
                    "mariadb",
                    "--no-defaults",
                    "--protocol=SOCKET",
                    f"--socket={socket}",
                    "--user=root",
                    "--local-infile=0",
                    "--binary-mode",
                ],
                stdin=decompress.stdout,
                stdout=log,
                stderr=log,
            )
            processes.append(client)
            decompress.stdout.close()
            if client.wait(timeout=300) != 0 or decompress.wait(timeout=30) != 0:
                raise ValueError("Isolated import failed; inspect retained import log")
        config = MariaDbConnectionConfig(
            host="localhost", user="root", unix_socket=str(socket), use_client=True
        )
        actual = fingerprint(config, "noctivar")
        if actual != expected:
            raise ValueError(
                "Restored DDL/ordered typed-row fingerprints differ from the source"
            )
        with (
            pymysql.connect(
                unix_socket=str(socket), user="root", database="noctivar"
            ) as c,
            c.cursor() as cur,
        ):
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA='noctivar'"
            )
            foreign_keys = cur.fetchone()[0]
            cur.execute(
                "SELECT migration_id,content_sha256,state FROM noctivar_migration_history ORDER BY migration_id"
            )
            history = cur.fetchall()
        result = {
            "status": "restored_verified",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "backup_directory": str(backup),
            "backup_id": manifest["backup_id"],
            "backup_manifest_sha256": hashlib.sha256(
                (backup / "manifest.json").read_bytes()
            ).hexdigest(),
            "source_fingerprint_sha256": hashlib.sha256(
                (backup / "SOURCE_FINGERPRINT.json").read_bytes()
            ).hexdigest(),
            "isolated_root": str(root),
            "socket": str(socket),
            "networking": "disabled",
            "object_scope": manifest.get("object_scope", "complete_schema"),
            "table_fingerprints": actual["tables"],
            "foreign_keys": foreign_keys,
            "migration_history": history,
            "scope": "All selected table DDL and typed rows restored and matched. Excluded object classes remain unqualified. No application or full disaster recovery claim.",
        }
        durable_replace_text(receipt, json.dumps(result, indent=2) + "\n")
        return result
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        # Retain datadir/logs for review; never delete recovery evidence here.


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backup", type=Path, required=True)
    p.add_argument("--receipt", type=Path, required=True)
    a = p.parse_args()
    r = rehearse(a.backup, a.receipt)
    print(
        json.dumps(
            {
                "status": r["status"],
                "object_scope": r["object_scope"],
                "tables": len(r["table_fingerprints"]),
                "foreign_keys": r["foreign_keys"],
                "receipt": str(a.receipt),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
