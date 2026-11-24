"""Stream compressed SQL dumps into the MariaDB client."""

from __future__ import annotations

import gzip
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.mariadb.identifiers import assert_safe_identifier

from mercury.database.mariadb.sql_transform import SqlTransformer

# Progress callback: (bytes_written, compressed_dump_size, elapsed_seconds).
ImportProgressCallback = Callable[[int, int, float], None]
# Bound lexer expansion; reject unsupported giant physical lines before DDL
# during deployment validation rather than exhausting memory while importing.
MAX_SQL_LINE_BYTES = 8 * 1024 * 1024

# Session knobs that speed bulk restores without requiring SUPER.
# Mysqldump often already emits similar SETs; repeating is harmless and covers
# dumps that omit them.
_IMPORT_SESSION_PREAMBLE = (
    b"SET SESSION unique_checks=0;\n"
    b"SET SESSION foreign_key_checks=0;\n"
)


@contextmanager
def _open_dump_lines(dump_path: Path) -> Iterator[BinaryIO]:
    """Yield a binary line iterator; prefer pigz -dc for multi-core gunzip."""
    if not str(dump_path).endswith(".gz"):
        with open(dump_path, "rb") as handle:
            yield handle
        return

    pigz = shutil.which("pigz")
    if pigz is None:
        with gzip.open(dump_path, "rb") as handle:
            yield handle
        return

    proc = subprocess.Popen(
        [pigz, "-dc", str(dump_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.stdout is None:
        proc.kill()
        proc.wait()
        raise BackupExecutionError("pigz stdout pipe was not created")
    try:
        yield proc.stdout
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
        stderr = b""
        if proc.stderr is not None:
            try:
                stderr = proc.stderr.read()
            finally:
                proc.stderr.close()
        if proc.poll() is None:
            proc.kill()
        code = proc.wait()
        # Positive exit = pigz error. Negative/signal (SIGPIPE/SIGKILL) is normal
        # when the import aborts early.
        if code > 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise BackupExecutionError(detail or f"pigz -dc failed (exit {code})")


def _bounded_lines(stream: BinaryIO) -> Iterator[bytes]:
    for line in iter(lambda: stream.readline(MAX_SQL_LINE_BYTES + 1), b""):
        if len(line) > MAX_SQL_LINE_BYTES:
            raise ValueError("SQL physical line exceeds the supported 8 MiB limit; import refused")
        yield line


def _next_quoted_span(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return len(text)


def collect_sql_code(text: str) -> str:
    """Return SQL with string literals and ordinary comments removed.

    Versioned MariaDB comments (/*!12345 ... */) remain because they execute.
    """
    parts: list[str] = []
    index = 0
    code_start = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch in {"'", '"'}:
            parts.append(text[code_start:index])
            index = _next_quoted_span(text, index)
            code_start = index
            continue
        if ch == "#" or (ch == "-" and index + 1 < length and text[index + 1] == "-"):
            parts.append(text[code_start:index])
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
            code_start = index
            continue
        if ch == "/" and index + 1 < length and text[index + 1] == "*":
            versioned = index + 3 < length and text[index + 2] == "!" and text[index + 3].isdigit()
            if versioned:
                index += 1
                continue
            parts.append(text[code_start:index])
            closer = text.find("*/", index + 2)
            index = length if closer < 0 else closer + 2
            code_start = index
            continue
        index += 1
    parts.append(text[code_start:])
    return "".join(parts)


def _transform_sql_line(
    line: str,
    *,
    strip_definer: bool,
    strip_database_directives: bool,
    rewrite_database: tuple[str, str] | None = None,
    rewrite_databases: Mapping[str, str] | None = None,
) -> str:
    """Single-input convenience wrapper; streaming imports retain lexer state."""
    rewrites = dict(rewrite_databases or {})
    if rewrite_database is not None:
        rewrites[rewrite_database[0]] = rewrite_database[1]
    transformer = SqlTransformer(strip_definer=strip_definer,
        strip_database_directives=strip_database_directives, rewrites=rewrites)
    return transformer.transform(line)


def validate_sql_transform(dump_path: Path, *, source: str, target: str) -> None:
    """Reject unsupported transformations before fresh-target deployment DDL."""
    transformer = SqlTransformer(strip_definer=True, strip_database_directives=True,
        rewrites={source: target})
    with _open_dump_lines(dump_path) as lines:
        for line in _bounded_lines(lines):
            transformer.transform(line.decode("latin-1"))
    transformer.finish()


def run_compressed_sql_import(
    argv: list[str],
    env: dict[str, str],
    dump_path: Path,
    *,
    strip_definer: bool = True,
    strip_database_directives: bool = True,
    rewrite_database: tuple[str, str] | None = None,
    rewrite_databases: Mapping[str, str] | None = None,
    on_progress: ImportProgressCallback | None = None,
    progress_every_bytes: int = 16 * 1024 * 1024,
    session_preamble: bool = True,
) -> None:
    """
    Stream a dump into ``mariadb target`` with safe SQL rewrites.

    Strips literal DEFINER clauses by default so unix_socket operators without
    SET USER can import mysqldump artifacts from other hosts. The receiving
    account becomes the routine definer, while SQL SECURITY semantics are
    preserved; changing DEFINER to INVOKER would alter application behavior.
    Strips ``CREATE/DROP/ALTER DATABASE`` (and ``SCHEMA``) plus ``USE``
    statements by default so targeted restore/sync imports cannot switch to or
    drop another catalog and instead land in the requested dev or restore-check
    database.

    A stateful lexer preserves strings, comments, and arbitrary data bytes,
    including across physical lines. Unsupported quoting modes or split
    transformation clauses fail closed. No line-based INSERT bypass may skip
    the lexical state of a routine or a multiline literal.
    """
    if dump_path.is_symlink():
        raise BackupExecutionError(f"Refusing symlink dump path: {dump_path}")
    if not dump_path.is_file():
        raise BackupExecutionError(f"Dump file not found: {dump_path}")

    compressed_size = dump_path.stat().st_size
    started = time.monotonic()
    try:
        if rewrite_database is not None:
            assert_safe_identifier(rewrite_database[0], what="schema rewrite source")
            assert_safe_identifier(rewrite_database[1], what="schema rewrite target")
        if rewrite_databases:
            for source, target in rewrite_databases.items():
                assert_safe_identifier(source, what="schema rewrite source")
                assert_safe_identifier(target, what="schema rewrite target")
    except ValueError as exc:
        raise BackupExecutionError(str(exc)) from exc
    rewrites = dict(rewrite_databases or {})
    if rewrite_database is not None:
        rewrites[rewrite_database[0]] = rewrite_database[1]
    transformer = SqlTransformer(strip_definer=strip_definer,
        strip_database_directives=strip_database_directives, rewrites=rewrites)

    import_proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
        text=False,
        bufsize=1024 * 1024,
    )

    stdin = import_proc.stdin
    if stdin is None:
        import_proc.kill()
        import_proc.wait()
        raise BackupExecutionError("MariaDB import stdin pipe was not created")
    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        err = import_proc.stderr
        if err is None:
            return
        while True:
            chunk = err.read(65536)
            if not chunk:
                break
            stderr_chunks.append(chunk)

    stderr_thread = threading.Thread(target=_drain_stderr, name="mariadb-import-stderr", daemon=True)
    stderr_thread.start()

    last_progress_at = 0
    bytes_written = 0

    def _write(payload: bytes) -> bool:
        nonlocal bytes_written, last_progress_at
        try:
            stdin.write(payload)
        except (BrokenPipeError, ValueError):
            return False
        bytes_written += len(payload)
        if (
            on_progress is not None
            and bytes_written - last_progress_at >= progress_every_bytes
        ):
            on_progress(
                bytes_written,
                compressed_size,
                time.monotonic() - started,
            )
            last_progress_at = bytes_written
        return True

    try:
        if session_preamble and not _write(_IMPORT_SESSION_PREAMBLE):
            pass
        else:
            with _open_dump_lines(dump_path) as lines:
                for raw_line in _bounded_lines(lines):
                    # latin-1 is a byte-preserving transport, not a claim about
                    # the dump charset. SQL control tokens/schema names are ASCII.
                    rewritten = transformer.transform(raw_line.decode("latin-1"))
                    if rewritten and not _write(rewritten.encode("latin-1")):
                        break
                else:
                    transformer.finish()
    except (OSError, ValueError, EOFError) as exc:
        import_proc.kill()
        import_proc.wait()
        stderr_thread.join(timeout=5)
        raise BackupExecutionError(str(exc)) from exc
    finally:
        try:
            stdin.close()
        except (OSError, ValueError):
            pass
        # communicate() on Python 3.12 still flushes stdin if the handle is set,
        # even after we closed it ourselves while streaming.
        import_proc.stdin = None

    stderr_thread.join(timeout=60)
    returncode = import_proc.wait()
    import_err = b"".join(stderr_chunks)

    if returncode != 0:
        detail = import_err.decode().strip()
        raise BackupExecutionError(detail or "mariadb import failed")
