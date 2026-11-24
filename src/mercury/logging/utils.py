"""Logging utilities — sessions, operations, and log file helpers."""

from __future__ import annotations

import os
import re
import shlex
import sys
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from mercury.logging.config import (
    KNOWN_LOG_FILENAMES,
    LOGGER_NAME,
)
from mercury.logging.engine import (
    active_log_directory,
    begin_session,
    current_log_file,
    finish_session,
    get_logger,
    session_id,
)

_SENSITIVE_OPTIONS = frozenset({
    "--password", "--passwd", "--api-key", "--access-token", "--token",
    "--secret", "--credential", "--credentials", "--credentials-json",
})


def _safe_command(argv: list[str]) -> str:
    # Search terms can contain copied credentials. Record the action only.
    for index in range(1, len(argv) - 1):
        if argv[index:index + 2] == ["logs", "search"]:
            argv = argv[:index + 2]
            break
    safe: list[str] = []
    arguments = iter(argv)
    for arg in arguments:
        option, separator, _ = arg.partition("=")
        if option.casefold().replace("_", "-") in _SENSITIVE_OPTIONS:
            safe.append(f"{option}=<redacted>")
            if not separator:
                next(arguments, None)
        else:
            safe.append(arg.replace("\r", r"\r").replace("\n", r"\n"))
    return " ".join(arg if arg.endswith("=<redacted>") else shlex.quote(arg) for arg in safe)


def format_fields(**fields: object) -> str:
    """Space-separated key=value suffix for log messages."""
    if not fields:
        return ""
    return " " + " ".join(f"{key}={value!r}" for key, value in fields.items())


def policy_log_fields() -> dict[str, object]:
    try:
        from mercury.core.execution_policy import load_execution_policy

        policy = load_execution_policy()
        return {
            "dry_run": policy.dry_run,
            "live_actions": policy.live_actions_enabled,
            "backup_root": str(policy.backup_root),
        }
    except Exception:
        return {}


def log_session_start(*, argv: list[str] | None = None) -> None:
    """Record the start of a CLI or menu session."""
    command = _safe_command(sys.argv if argv is None else argv)
    identifier = begin_session()
    get_logger().info(
        "session start id=%s pid=%s cwd=%s command=%s%s",
        identifier,
        os.getpid(),
        os.getcwd(),
        command,
        format_fields(**policy_log_fields()),
    )


def log_session_end(*, exit_code: int = 0, detail: str | None = None) -> None:
    """Record normal or error exit of a CLI or menu session."""
    elapsed_ms = finish_session()
    if elapsed_ms is None:
        return
    logger = get_logger()
    if exit_code == 0:
        write_event = logger.info
    elif exit_code in (2, 130):
        write_event = logger.warning
    else:
        write_event = logger.error
    write_event(
        "session end id=%s exit_code=%s elapsed_ms=%.1f%s",
        session_id(), exit_code, elapsed_ms,
        format_fields(detail=detail) if detail else "",
    )


def log_uncaught_exception(exc: BaseException | None = None) -> None:
    """Record an unhandled exception with traceback."""
    get_logger("mercury.error").exception(
        "uncaught exception id=%s",
        session_id(),
        exc_info=exc if exc is not None else True,
    )


def log_fields(level: int, message: str, **fields: object) -> None:
    """Log a message with ``key=value`` fields appended."""
    suffix = format_fields(**fields)
    get_logger().log(level, f"{message}{suffix}" if suffix else message)


@contextmanager
def log_operation(operation: str, *, logger_name: str = LOGGER_NAME, **fields: object) -> Iterator[None]:
    """Context manager that logs operation start, success, or failure with timing."""
    log = get_logger(logger_name)
    suffix = format_fields(**fields)
    started = time.perf_counter()
    log.info("operation start operation=%s%s", operation, suffix)
    try:
        yield
    except KeyboardInterrupt:
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.warning("operation interrupted operation=%s elapsed_ms=%.1f%s", operation, elapsed_ms, suffix)
        raise
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log.exception(
            "operation failed operation=%s elapsed_ms=%s error=%s%s",
            operation,
            elapsed_ms,
            exc,
            suffix,
        )
        raise
    else:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info("operation ok operation=%s elapsed_ms=%s%s", operation, elapsed_ms, suffix)


def list_log_files(*, log_dir: Path | None = None) -> list[Path]:
    """Main daily session logs (``mercury-*.log``), newest first."""
    directory = log_dir or active_log_directory()
    if not directory.is_dir():
        return []
    return sorted((path for path in directory.glob("mercury-*.log") if path.is_file()), reverse=True)


def list_all_log_files(*, log_dir: Path | None = None) -> list[Path]:
    """All Mercury log files including dedicated error/database/backup logs."""
    directory = log_dir or active_log_directory()
    if not directory.is_dir():
        return []

    paths: list[Path] = []
    for name in KNOWN_LOG_FILENAMES:
        path = directory / name
        if path.is_file():
            paths.append(path)
    paths.extend(list_log_files(log_dir=directory))
    dated: list[tuple[float, Path]] = []
    for path in paths:
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return [path for _, path in sorted(dated, reverse=True)]


def read_log_tail(*, lines: int = 50, log_file: Path | None = None) -> list[str]:
    path = log_file or current_log_file() or (list_log_files()[:1] or [None])[0]
    if path is None or not path.is_file() or lines <= 0:
        return []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return [line.rstrip("\r\n") for line in deque(handle, maxlen=lines)]
    except OSError:
        return []


def search_log_files(
    pattern: str,
    *,
    log_dir: Path | None = None,
    log_file: Path | None = None,
    max_files: int = 14,
    max_matches: int = 200,
    ignore_case: bool = True,
) -> list[tuple[Path, int, str]]:
    """Search recent log files; returns (file, line_number, line_text)."""
    if max_matches <= 0 or max_files <= 0:
        return []
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(re.escape(pattern), flags)
    matches: list[tuple[Path, int, str]] = []

    paths = [log_file] if log_file is not None else list_all_log_files(log_dir=log_dir)[:max_files]
    for path in paths:
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if regex.search(line):
                        matches.append((path, line_number, line.rstrip("\r\n")))
                        if len(matches) >= max_matches:
                            return matches
        except OSError:
            continue
    return matches
