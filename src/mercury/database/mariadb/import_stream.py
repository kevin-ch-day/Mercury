"""Stream compressed SQL dumps into the MariaDB client."""

from __future__ import annotations

import gzip
import re
import subprocess
from pathlib import Path

from mercury.backup.backup_runner import BackupExecutionError

_DEFINER_RE = re.compile(r"DEFINER=`[^`]+`@`[^`]+`\s*", re.IGNORECASE)
_SQL_SECURITY_DEFINER_RE = re.compile(r"SQL SECURITY DEFINER", re.IGNORECASE)
_CREATE_DATABASE_RE = re.compile(r"^\s*CREATE\s+DATABASE\b", re.IGNORECASE)
_USE_DATABASE_RE = re.compile(r"^\s*USE\s+[`'\"]?[\w$-]+[`'\"]?\s*;", re.IGNORECASE)


def _transform_sql_line(
    line: str,
    *,
    strip_definer: bool,
    strip_database_directives: bool,
) -> str:
    text = line
    if strip_database_directives and (
        _CREATE_DATABASE_RE.match(text) or _USE_DATABASE_RE.match(text)
    ):
        return ""
    if strip_definer:
        text = _DEFINER_RE.sub("*", text)
        text = _SQL_SECURITY_DEFINER_RE.sub("SQL SECURITY INVOKER", text)
    return text


def run_compressed_sql_import(
    argv: list[str],
    env: dict[str, str],
    dump_path: Path,
    *,
    strip_definer: bool = True,
    strip_database_directives: bool = True,
) -> None:
    """
    Stream a dump into ``mariadb target`` with safe SQL rewrites.

    Strips DEFINER clauses by default so unix_socket operators without SET USER
    can import mysqldump artifacts from other hosts.
    Strips ``CREATE DATABASE`` / ``USE`` statements by default so targeted
    restore/sync imports land in the requested dev or restore-check database
    instead of switching back to the original production database name.
    """
    if not dump_path.is_file():
        raise BackupExecutionError(f"Dump file not found: {dump_path}")

    import_proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=False,
    )

    assert import_proc.stdin is not None
    try:
        opener = gzip.open if str(dump_path).endswith(".gz") else open
        with opener(dump_path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                rewritten = _transform_sql_line(
                    line,
                    strip_definer=strip_definer,
                    strip_database_directives=strip_database_directives,
                )
                if not rewritten:
                    continue
                import_proc.stdin.write(rewritten.encode("utf-8"))
        import_proc.stdin.close()
    except OSError as exc:
        import_proc.kill()
        import_proc.wait()
        raise BackupExecutionError(str(exc)) from exc

    _, import_err = import_proc.communicate()

    if import_proc.returncode != 0:
        detail = (import_err or b"").decode().strip()
        raise BackupExecutionError(detail or "mariadb import failed")
