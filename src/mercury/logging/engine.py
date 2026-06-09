"""Logging engine — handler setup, logger access, and runtime state."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

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
_log_file: Path | None = None
_error_log_file: Path | None = None
_database_log_file: Path | None = None
_backup_log_file: Path | None = None
_configured_loggers: list[str] = []


def session_id() -> str:
    global _session_id
    if _session_id is None:
        _session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return _session_id


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


def log_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s [%(session_id)s] %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        defaults={"session_id": "-"},
    )


def attach_file_handler(logger: logging.Logger, path: Path, *, level: int) -> logging.FileHandler:
    handler = logging.FileHandler(path, encoding="utf-8")
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

    if _configured:
        return _log_file

    if not logging_enabled(override=enabled):
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        return None

    resolved_dir = resolve_log_dir(override=log_dir)
    from mercury.core.path_permissions import check_path_permission, chown_repair_command

    log_check = check_path_permission(resolved_dir, label="log directory")
    if log_check.needs_repair:
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        repair = chown_repair_command(resolved_dir) if resolved_dir.exists() else "./run.sh doctor --repair-plan"
        print(
            f"Mercury: cannot write logs under {resolved_dir} ({log_check.detail}). "
            f"Continuing without file logging. Repair: {repair}",
            file=sys.stderr,
        )
        return None
    try:
        resolved_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        print(
            f"Mercury: cannot write logs to {resolved_dir} (permission denied). "
            "Continuing without file logging. Run: ./run.sh doctor --repair-plan",
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

    file_level = resolve_log_level(override=str(level) if level is not None else None)

    root = logging.getLogger(LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    root.propagate = False
    clear_logger(LOGGER_NAME)
    try:
        attach_file_handler(root, log_path, level=file_level)
        attach_file_handler(root, error_path, level=logging.ERROR)

        database_logger = logging.getLogger(DATABASE_LOGGER_NAME)
        database_logger.setLevel(logging.DEBUG)
        database_logger.propagate = True
        clear_logger(DATABASE_LOGGER_NAME)
        attach_file_handler(database_logger, database_path, level=logging.DEBUG)

        backup_logger = logging.getLogger(BACKUP_LOGGER_NAME)
        backup_logger.setLevel(logging.DEBUG)
        backup_logger.propagate = True
        clear_logger(BACKUP_LOGGER_NAME)
        attach_file_handler(backup_logger, backup_path, level=logging.DEBUG)

        error_logger = logging.getLogger(ERROR_LOGGER_NAME)
        error_logger.setLevel(logging.DEBUG)
        error_logger.propagate = True
    except PermissionError:
        clear_logger(LOGGER_NAME)
        clear_logger(DATABASE_LOGGER_NAME)
        clear_logger(BACKUP_LOGGER_NAME)
        _configured = True
        _log_file = None
        _error_log_file = None
        _database_log_file = None
        _backup_log_file = None
        print(
            f"Mercury: cannot write logs under {resolved_dir} (permission denied). "
            "Continuing without file logging. Run: ./run.sh doctor --repair-plan",
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


def reset_logging() -> None:
    """Clear handlers (tests only)."""
    global _configured, _log_file, _error_log_file, _database_log_file, _backup_log_file, _session_id, _configured_loggers

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
    _session_id = None
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
