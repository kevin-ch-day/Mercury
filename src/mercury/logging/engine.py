"""Logging engine — handler setup, logger access, and runtime state."""

from __future__ import annotations

import errno
import logging
import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mercury.logging.config import (
    BACKUP_LOGGER_NAME,
    DATABASE_LOGGER_NAME,
    ERROR_LOGGER_NAME,
    LOGGER_NAME,
    backup_log_path,
    daily_log_path,
    database_log_path,
    error_log_path,
    logging_enabled,
    normalize_logger_name,
    resolve_log_dir,
    resolve_log_level,
)

_configured = False
_session_id: str | None = None
_session_started_at: float | None = None
_active_log_directory: Path | None = None
_log_file: Path | None = None
_error_log_file: Path | None = None
_database_log_file: Path | None = None
_backup_log_file: Path | None = None
_configured_loggers: list[str] = []


def session_id() -> str:
    global _session_id
    if _session_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        _session_id = f"{stamp}-{uuid4().hex[:8]}"
    return _session_id


def begin_session() -> str:
    global _session_id, _session_started_at
    _session_id = None
    _session_started_at = time.perf_counter()
    return session_id()


def finish_session() -> float | None:
    global _session_started_at
    started_at, _session_started_at = _session_started_at, None
    return None if started_at is None else (time.perf_counter() - started_at) * 1000


def active_log_directory() -> Path:
    return _active_log_directory or resolve_log_dir()


def current_log_file() -> Path | None:
    return _log_file


def current_error_log_file() -> Path | None:
    return _error_log_file


def current_database_log_file() -> Path | None:
    return _database_log_file


def current_backup_log_file() -> Path | None:
    return _backup_log_file


def is_configured() -> bool:
    return _configured


class SessionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = session_id()
        return True


class RedactingFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        from mercury.core.output import redact_sensitive_text

        return redact_sensitive_text(super().format(record))


class PrivateLogFileHandler(logging.FileHandler):
    def _open(self):
        def open_private(path: str, flags: int) -> int:
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(path, flags, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError(errno.EINVAL, "log target is not a regular file", path)
            except OSError:
                os.close(fd)
                raise
            return fd

        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors, opener=open_private)


def log_formatter() -> logging.Formatter:
    return RedactingFormatter(
        fmt="%(asctime)s [%(session_id)s] %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%SZ",
        defaults={"session_id": "-"},
    )


def attach_file_handler(logger: logging.Logger, path: Path, *, level: int) -> logging.FileHandler:
    handler = PrivateLogFileHandler(path, encoding="utf-8", errors="backslashreplace")
    handler.setLevel(level)
    handler.setFormatter(log_formatter())
    handler.addFilter(SessionFilter())
    logger.addHandler(handler)
    return handler


def clear_logger(name: str) -> None:
    logger = logging.getLogger(name)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    logger.propagate = name != LOGGER_NAME


def configure_logging(
    *,
    enabled: bool | None = None,
    level: str | int | None = None,
    log_dir: Path | str | None = None,
    console: bool = False,
    console_level: int = logging.WARNING,
) -> Path | None:
    """
    Configure the Mercury logger (idempotent).

    Returns the active main log file path when file logging is enabled.
    """
    global _configured, _log_file, _error_log_file, _database_log_file, _backup_log_file, _configured_loggers
    global _active_log_directory

    if _configured:
        return _log_file

    resolved_dir = resolve_log_dir(override=log_dir)
    _active_log_directory = resolved_dir
    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    clear_logger(LOGGER_NAME)
    root.addHandler(logging.NullHandler())
    if not logging_enabled(override=enabled):
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        return None

    from mercury.core.path_permissions import check_path_permission
    from mercury.core.usb_device import log_directory_repair_hint

    log_check = check_path_permission(resolved_dir, label="log directory")
    if log_check.needs_repair:
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        repair = log_directory_repair_hint(resolved_dir, permission_detail=log_check.detail)
        print(
            f"Mercury: cannot write logs under {resolved_dir} ({log_check.detail}). "
            f"Continuing without file logging. Repair: {repair}",
            file=sys.stderr,
        )
        return None
    from mercury.core.usb_mount import inactive_operator_mount_blocker

    inactive = inactive_operator_mount_blocker(resolved_dir)
    if inactive:
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        repair = log_directory_repair_hint(resolved_dir, permission_detail=inactive)
        print(
            f"Mercury: refusing log mkdir under inactive operator mount ({inactive}). "
            f"Continuing without file logging. Repair: {repair}",
            file=sys.stderr,
        )
        return None
    try:
        resolved_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        repair = log_directory_repair_hint(resolved_dir, permission_detail="permission denied")

        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        print(
            f"Mercury: cannot create log directory {resolved_dir} ({exc.strerror or type(exc).__name__}). "
            f"Continuing without file logging. Repair: {repair}",
            file=sys.stderr,
        )
        return None

    log_path = daily_log_path(log_dir=resolved_dir)
    error_path = error_log_path(log_dir=resolved_dir)
    database_path = database_log_path(log_dir=resolved_dir)
    backup_path = backup_log_path(log_dir=resolved_dir)
    _log_file = log_path
    _error_log_file = error_path
    _database_log_file = database_path
    _backup_log_file = backup_path

    file_level = resolve_log_level(override=level)

    try:
        attach_file_handler(root, log_path, level=file_level)
        attach_file_handler(root, error_path, level=logging.ERROR)

        database_logger = logging.getLogger(DATABASE_LOGGER_NAME)
        database_logger.setLevel(logging.DEBUG)
        database_logger.propagate = True
        clear_logger(DATABASE_LOGGER_NAME)
        attach_file_handler(database_logger, database_path, level=file_level)

        backup_logger = logging.getLogger(BACKUP_LOGGER_NAME)
        backup_logger.setLevel(logging.DEBUG)
        backup_logger.propagate = True
        clear_logger(BACKUP_LOGGER_NAME)
        attach_file_handler(backup_logger, backup_path, level=file_level)

        error_logger = logging.getLogger(ERROR_LOGGER_NAME)
        error_logger.setLevel(logging.DEBUG)
        error_logger.propagate = True
    except OSError as exc:
        clear_logger(LOGGER_NAME)
        root.addHandler(logging.NullHandler())
        clear_logger(DATABASE_LOGGER_NAME)
        clear_logger(BACKUP_LOGGER_NAME)
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        repair = log_directory_repair_hint(resolved_dir, permission_detail="permission denied")
        print(
            f"Mercury: cannot write logs under {resolved_dir} ({exc.strerror or type(exc).__name__}). "
            f"Continuing without file logging. Repair: {repair}",
            file=sys.stderr,
        )
        return None

    _configured_loggers = [LOGGER_NAME, DATABASE_LOGGER_NAME, BACKUP_LOGGER_NAME, ERROR_LOGGER_NAME]

    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(console_level)
        stream_handler.setFormatter(log_formatter())
        stream_handler.addFilter(SessionFilter())
        root.addHandler(stream_handler)

    _configured = True
    return log_path


def reset_logging(*, preserve_session: bool = False) -> None:
    """Clear handlers and active logging configuration."""
    global _configured, _log_file, _error_log_file, _database_log_file, _backup_log_file, _session_id, _configured_loggers
    global _active_log_directory, _session_started_at

    for name in _configured_loggers or [
        LOGGER_NAME,
        DATABASE_LOGGER_NAME,
        BACKUP_LOGGER_NAME,
        ERROR_LOGGER_NAME,
    ]:
        clear_logger(name)

    _configured = False
    _log_file = None
    _error_log_file = None
    _database_log_file = None
    _backup_log_file = None
    _active_log_directory = None
    if not preserve_session:
        _session_id = None
        _session_started_at = None
    _configured_loggers = []


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Return a Mercury logger, configuring file capture on first use."""
    if not _configured:
        configure_logging()

    full_name = normalize_logger_name(name)
    if full_name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)

    child = logging.getLogger(full_name)
    child.propagate = True
    return child
