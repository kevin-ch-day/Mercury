"""Logging utilities — sessions, operations, and log file helpers."""

from __future__ import annotations

import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from mercury.logging.config import (
    BACKUP_LOG_FILENAME,
    DATABASE_LOG_FILENAME,
    ERROR_LOG_FILENAME,
    KNOWN_LOG_FILENAMES,
    LOGGER_NAME,
    resolve_log_dir,
)
from mercury.logging.engine import (
    current_log_file,
    get_logger,
    session_id,
)


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
    command = " ".join(argv or sys.argv)
    get_logger().info(
        "session start id=%s pid=%s cwd=%s command=%s%s",
        session_id(),
        os.getpid(),
        os.getcwd(),
        command,
        format_fields(**policy_log_fields()),
    )


def log_session_end(*, exit_code: int = 0, detail: str | None = None) -> None:
    """Record normal or error exit of a CLI or menu session."""
    message = "session end id=%s exit_code=%s%s"
    args = (
        session_id(),
        exit_code,
        format_fields(detail=detail) if detail else "",
    )
    if exit_code != 0:
        get_logger().error(message, *args)
    else:
        get_logger().info(message, *args)


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
    directory = log_dir or resolve_log_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("mercury-*.log"), reverse=True)


def list_all_log_files(*, log_dir: Path | None = None) -> list[Path]:
    """All Mercury log files including dedicated error/database/backup logs."""
    directory = log_dir or resolve_log_dir()
    if not directory.is_dir():
        return []

    paths: list[Path] = []
    for name in KNOWN_LOG_FILENAMES:
        path = directory / name
        if path.is_file():
            paths.append(path)
    paths.extend(directory.glob("mercury-*.log"))
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def read_log_tail(*, lines: int = 50, log_file: Path | None = None) -> list[str]:
    path = log_file or current_log_file() or (list_log_files()[:1] or [None])[0]
    if path is None or not path.is_file():
        return []
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return content[-lines:]


def search_log_files(
    pattern: str,
    *,
    log_dir: Path | None = None,
    max_files: int = 14,
    max_matches: int = 200,
    ignore_case: bool = True,
) -> list[tuple[Path, int, str]]:
    """Search recent log files; returns (file, line_number, line_text)."""
    flags = re.IGNORECASE if ignore_case else 0
    regex = re.compile(re.escape(pattern), flags)
    matches: list[tuple[Path, int, str]] = []

    for path in list_all_log_files(log_dir=log_dir)[:max_files]:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            if regex.search(line):
                matches.append((path, line_number, line))
                if len(matches) >= max_matches:
                    return matches
    return matches
