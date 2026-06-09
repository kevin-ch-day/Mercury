"""Stream compressed SQL dumps into the MariaDB client."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mercury.backup.backup_runner import BackupExecutionError

# gzip exits 141 (128 + SIGPIPE) when mariadb stops reading early — not a dump read failure.
_GZIP_SIGPIPE_EXIT_CODES = {141, -13}


def run_compressed_sql_import(
    argv: list[str],
    env: dict[str, str],
    dump_path: Path,
    *,
    strip_definer: bool = True,
) -> None:
    """
    Run ``gzip -dc dump | [sed] | mariadb target``.

    Strips DEFINER clauses by default so unix_socket operators without SET USER
    can import mysqldump artifacts from other hosts.
    """
    if not dump_path.is_file():
        raise BackupExecutionError(f"Dump file not found: {dump_path}")

    if str(dump_path).endswith(".gz"):
        gzip_proc = subprocess.Popen(
            ["gzip", "-dc", str(dump_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        source_stdout = gzip_proc.stdout
        gzip_stderr = gzip_proc.stderr
    else:
        gzip_proc = None
        source_stdout = dump_path.open("rb")
        gzip_stderr = None

    assert source_stdout is not None
    stream_stdout = source_stdout
    sed_proc: subprocess.Popen[bytes] | None = None
    if strip_definer:
        sed_proc = subprocess.Popen(
            [
                "sed",
                "-E",
                "-e",
                r"s/DEFINER=[^*]+\*/\*/",
                "-e",
                "s/SQL SECURITY DEFINER/SQL SECURITY INVOKER/g",
            ],
            stdin=source_stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stream_stdout = sed_proc.stdout
        if gzip_proc is not None:
            source_stdout.close()

    assert stream_stdout is not None
    import_proc = subprocess.Popen(
        argv,
        stdin=stream_stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stream_stdout.close()
    _, import_err = import_proc.communicate()

    gzip_returncode: int | None = None
    gzip_err = b""
    if gzip_proc is not None:
        if gzip_stderr is not None:
            gzip_err = gzip_stderr.read() or b""
            gzip_stderr.close()
        gzip_returncode = gzip_proc.wait()
    else:
        source_stdout.close()
    if sed_proc is not None:
        sed_proc.wait()

    if import_proc.returncode != 0:
        detail = (import_err or b"").decode().strip()
        raise BackupExecutionError(detail or "mariadb import failed")

    if gzip_returncode is not None and gzip_returncode not in (0, *_GZIP_SIGPIPE_EXIT_CODES):
        detail = gzip_err.decode().strip()
        raise BackupExecutionError(detail or "gzip failed while reading backup dump")
