"""Stream compressed SQL dumps into the MariaDB client."""

from __future__ import annotations

import gzip
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from mercury.backup.backup_runner import BackupExecutionError
from mercury.database.mariadb.identifiers import assert_safe_identifier

_DEFINER_RE = re.compile(r"DEFINER=`[^`]+`@`[^`]+`\s*", re.IGNORECASE)
_CONDITIONAL_DEFINER_COMMENT_RE = re.compile(
    r"/\*!50017\s+DEFINER=`[^`]+`@`[^`]+`\s*\*/",
    re.IGNORECASE,
)
_CREATE_DATABASE_RE = re.compile(r"^\s*CREATE\s+DATABASE\b", re.IGNORECASE)
_USE_DATABASE_RE = re.compile(r"^\s*USE\s+[`'\"]?[\w$-]+[`'\"]?\s*;", re.IGNORECASE)
_INSERT_OR_REPLACE_RE = re.compile(r"^\s*(INSERT|REPLACE)\b", re.IGNORECASE)

# Progress callback: (bytes_written, compressed_dump_size, elapsed_seconds).
ImportProgressCallback = Callable[[int, int, float], None]

# Session knobs that speed bulk restores without requiring SUPER.
# Mysqldump often already emits similar SETs; repeating is harmless and covers
# dumps that omit them.
_IMPORT_SESSION_PREAMBLE = (
    b"SET SESSION unique_checks=0;\n"
    b"SET SESSION foreign_key_checks=0;\n"
)


@contextmanager
def _open_dump_lines(dump_path: Path) -> Iterator[Iterator[bytes]]:
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
    assert proc.stdout is not None
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


def _rewrite_code_identifiers(segment: str, source: str, target: str) -> str:
    # Only rewrite schema-qualified identifiers.  Do not replace arbitrary
    # SQL string data that happens to contain a database name.
    segment = re.sub(
        rf"`{re.escape(source)}`(?=\s*\.)",
        f"`{target}`",
        segment,
    )
    return re.sub(rf"(?<![\w$]){re.escape(source)}(?=\.)", target, segment)


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


def _rewrite_database_name(text: str, source: str, target: str) -> str:
    if source == target:
        return text

    # Rewrite code segments only.  Preserve string literals and ordinary
    # comments.  MariaDB versioned comments (/*!12345 ... */) are executable
    # and must still be rewritten.
    parts: list[str] = []
    index = 0
    code_start = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch in {"'", '"'}:
            parts.append(_rewrite_code_identifiers(text[code_start:index], source, target))
            end = _next_quoted_span(text, index)
            parts.append(text[index:end])
            index = end
            code_start = index
            continue
        if ch == "#" or (ch == "-" and index + 1 < length and text[index + 1] == "-"):
            parts.append(_rewrite_code_identifiers(text[code_start:index], source, target))
            newline = text.find("\n", index)
            end = length if newline < 0 else newline + 1
            parts.append(text[index:end])
            index = end
            code_start = index
            continue
        if ch == "/" and index + 1 < length and text[index + 1] == "*":
            versioned = index + 3 < length and text[index + 2] == "!" and text[index + 3].isdigit()
            if versioned:
                index += 1
                continue
            parts.append(_rewrite_code_identifiers(text[code_start:index], source, target))
            closer = text.find("*/", index + 2)
            end = length if closer < 0 else closer + 2
            parts.append(text[index:end])
            index = end
            code_start = index
            continue
        index += 1
    parts.append(_rewrite_code_identifiers(text[code_start:], source, target))
    return "".join(parts)


def _statement_head_before_literals(text: str) -> str:
    """SQL before the first string literal — enough to see INSERT table refs."""
    single = text.find("'")
    double = text.find('"')
    cuts = [index for index in (single, double) if index >= 0]
    if not cuts:
        return text
    return text[: min(cuts)]


def _schema_qualified_source_present(text: str, sources: tuple[str, ...]) -> bool:
    """True when any source appears as a schema-qualified identifier."""
    scan = (
        _statement_head_before_literals(text)
        if _INSERT_OR_REPLACE_RE.match(text)
        else text
    )
    for source in sources:
        if f"`{source}`." in scan:
            return True
        if re.search(rf"(?<![\w$]){re.escape(source)}\.", scan):
            return True
    return False


def _transform_sql_line(
    line: str,
    *,
    strip_definer: bool,
    strip_database_directives: bool,
    rewrite_database: tuple[str, str] | None = None,
    rewrite_databases: Mapping[str, str] | None = None,
    rewrite_sources: tuple[str, ...] = (),
) -> str:
    text = line
    if strip_database_directives and (
        _CREATE_DATABASE_RE.match(text) or _USE_DATABASE_RE.match(text)
    ):
        return ""

    # Bulk INSERT/REPLACE rows dominate restore-check time. Skip DEFINER/security
    # regex work and skip schema rewrite unless the statement head is qualified.
    is_insert = bool(_INSERT_OR_REPLACE_RE.match(text))
    if strip_definer and not is_insert:
        text = _CONDITIONAL_DEFINER_COMMENT_RE.sub("", text)
        text = _DEFINER_RE.sub("", text)

    sources = rewrite_sources
    if not sources:
        collected: list[str] = []
        if rewrite_database is not None and rewrite_database[0] != rewrite_database[1]:
            collected.append(rewrite_database[0])
        if rewrite_databases:
            collected.extend(
                source
                for source, target in rewrite_databases.items()
                if source != target
            )
        sources = tuple(collected)

    if sources and _schema_qualified_source_present(text, sources):
        if rewrite_database is not None:
            text = _rewrite_database_name(text, rewrite_database[0], rewrite_database[1])
        if rewrite_databases:
            for source, target in rewrite_databases.items():
                text = _rewrite_database_name(text, source, target)
    return text


def _lstrip_sql_bytes(raw: bytes) -> bytes:
    index = 0
    length = len(raw)
    while index < length and raw[index] in b" \t\r\n":
        index += 1
    return raw[index:]


def _is_insert_or_replace_bytes(raw: bytes) -> bool:
    head = _lstrip_sql_bytes(raw)[:7].upper()
    return head.startswith(b"INSERT") or head.startswith(b"REPLACE")


def _is_database_directive_bytes(raw: bytes) -> bool:
    head = _lstrip_sql_bytes(raw).upper()
    for keyword in (
        b"CREATE DATABASE",
        b"CREATE SCHEMA",
        b"DROP DATABASE",
        b"DROP SCHEMA",
        b"ALTER DATABASE",
        b"ALTER SCHEMA",
    ):
        if head.startswith(keyword) and (
            len(head) == len(keyword) or head[len(keyword) : len(keyword) + 1] in b" \t`'\""
        ):
            return True
    if head.startswith(b"USE ") or head.startswith(b"USE`") or head.startswith(b"USE'") or head.startswith(
        b'USE"'
    ):
        return True
    return False


def _insert_head_before_literals_bytes(raw: bytes) -> bytes:
    single = raw.find(b"'")
    double = raw.find(b'"')
    cuts = [index for index in (single, double) if index >= 0]
    if not cuts:
        return raw
    return raw[: min(cuts)]


def _sql_statement_ends(raw: bytes) -> bool:
    """True when this physical line ends a SQL statement (trailing ';')."""
    return raw.rstrip().endswith(b";")


def _schema_markers_in_bytes(scan: bytes, markers: tuple[bytes, ...]) -> bool:
    return any(marker in scan for marker in markers)


def _line_needs_slow_transform(
    raw: bytes,
    *,
    schema_markers: tuple[bytes, ...],
    strip_definer: bool,
) -> bool:
    """Whether a non-INSERT line must be decoded for DEFINER/schema rewrite."""
    if strip_definer and (b"DEFINER" in raw or b"definer" in raw):
        return True
    if schema_markers and _schema_markers_in_bytes(raw, schema_markers):
        return True
    return False


def _build_rewrite_sources(
    rewrite_database: tuple[str, str] | None,
    rewrite_databases: Mapping[str, str] | None,
) -> tuple[str, ...]:
    sources: list[str] = []
    if rewrite_database is not None and rewrite_database[0] != rewrite_database[1]:
        sources.append(rewrite_database[0])
    if rewrite_databases:
        sources.extend(
            source
            for source, target in rewrite_databases.items()
            if source != target
        )
    return tuple(sources)


def _build_schema_byte_markers(sources: tuple[str, ...]) -> tuple[bytes, ...]:
    """Byte markers for schema-qualified ``source.`` / `` `source`. `` forms."""
    markers: list[bytes] = []
    for source in sources:
        encoded = source.encode("utf-8")
        markers.append(b"`" + encoded + b"`.")
        markers.append(encoded + b".")
    return tuple(markers)


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

    Hot path: MariaDB dumps use multi-line ``INSERT`` statements (VALUES row
    per line). After the opening ``INSERT``/``REPLACE`` line is handled, all
    continuation rows are forwarded as raw gzip bytes until the terminating
    ``;`` — this is the dominant path for multi‑GB restore-checks (ScytaleDroid
    has millions of continuation lines vs a few thousand INSERT headers).
    """
    if dump_path.is_symlink():
        raise BackupExecutionError(f"Refusing symlink dump path: {dump_path}")
    if not dump_path.is_file():
        raise BackupExecutionError(f"Dump file not found: {dump_path}")

    compressed_size = dump_path.stat().st_size
    started = time.monotonic()
    rewrite_sources = _build_rewrite_sources(rewrite_database, rewrite_databases)
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
    schema_markers = _build_schema_byte_markers(rewrite_sources)

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
    assert stdin is not None
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
    # After an INSERT/REPLACE header, value rows are always safe to passthrough.
    in_insert_passthrough = False

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
                for raw_line in lines:
                    if in_insert_passthrough:
                        if not _write(raw_line):
                            break
                        if _sql_statement_ends(raw_line):
                            in_insert_passthrough = False
                        continue

                    if _is_insert_or_replace_bytes(raw_line):
                        needs_header_rewrite = bool(
                            schema_markers
                            and _schema_markers_in_bytes(
                                _insert_head_before_literals_bytes(raw_line),
                                schema_markers,
                            )
                        )
                        if needs_header_rewrite:
                            line = raw_line.decode("utf-8", errors="replace")
                            rewritten = _transform_sql_line(
                                line,
                                strip_definer=False,
                                strip_database_directives=False,
                                rewrite_database=rewrite_database,
                                rewrite_databases=rewrite_databases,
                                rewrite_sources=rewrite_sources,
                            )
                            if rewritten and not _write(rewritten.encode("utf-8")):
                                break
                        else:
                            if not _write(raw_line):
                                break
                        # Multi-line INSERT: remaining value rows are passthrough.
                        if not _sql_statement_ends(raw_line):
                            in_insert_passthrough = True
                        continue

                    if strip_database_directives and _is_database_directive_bytes(
                        raw_line
                    ):
                        continue

                    # DDL / SET / comments without DEFINER or schema markers.
                    if not _line_needs_slow_transform(
                        raw_line,
                        schema_markers=schema_markers,
                        strip_definer=strip_definer,
                    ):
                        if not _write(raw_line):
                            break
                        continue

                    # Slow path: views, triggers, routines, schema-qualified DDL.
                    line = raw_line.decode("utf-8", errors="replace")
                    rewritten = _transform_sql_line(
                        line,
                        strip_definer=strip_definer,
                        strip_database_directives=False,
                        rewrite_database=rewrite_database,
                        rewrite_databases=rewrite_databases,
                        rewrite_sources=rewrite_sources,
                    )
                    if not rewritten:
                        continue
                    if not _write(rewritten.encode("utf-8")):
                        break
    except OSError as exc:
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
