"""Logging configuration — constants, paths, and settings resolution."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.paths import LOGS_DIR, REPO_ROOT, resolve_local_config

LOGGER_NAME = "mercury"
DATABASE_LOGGER_NAME = f"{LOGGER_NAME}.database"
BACKUP_LOGGER_NAME = f"{LOGGER_NAME}.backup"
ERROR_LOGGER_NAME = f"{LOGGER_NAME}.error"

MAIN_LOG_PREFIX = "mercury"
ERROR_LOG_FILENAME = "error.log"
DATABASE_LOG_FILENAME = "database.log"
BACKUP_LOG_FILENAME = "backup.log"

ENV_LOGGING_ENABLED = "MERCURY_LOGGING"
ENV_LOG_LEVEL = "MERCURY_LOG_LEVEL"
ENV_LOG_DIR = "MERCURY_LOG_DIR"

KNOWN_LOG_FILENAMES = (
    ERROR_LOG_FILENAME,
    DATABASE_LOG_FILENAME,
    BACKUP_LOG_FILENAME,
)


def env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_mercury_section() -> dict[str, object]:
    config_path = resolve_local_config()
    import tomllib

    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    section = data.get("mercury")
    if isinstance(section, dict):
        return section
    return {}


def parse_level(value: str) -> int:
    normalized = value.strip().upper()
    level = logging.getLevelNamesMapping().get(normalized)
    if isinstance(level, int):
        return level
    return logging.INFO


def resolve_log_dir(*, override: Path | str | None = None) -> Path:
    configured = str(override or "").strip() or os.environ.get(ENV_LOG_DIR, "").strip()
    if not configured:
        configured = str(load_mercury_section().get("log_dir") or "").strip()
    path = Path(configured).expanduser() if configured else LOGS_DIR
    resolved = (REPO_ROOT / path).resolve()

    # Detach / write-disabled: never open new log files on the Mercury HDD.
    try:
        from mercury.storage.detach_logging import default_detach_log_dir
        from mercury.storage.host_maintenance import path_is_under_primary_mount, writes_allowed

        if path_is_under_primary_mount(resolved) and not writes_allowed():
            return default_detach_log_dir().resolve()
    except Exception:
        pass
    return resolved


def resolve_log_level(*, override: str | int | None = None) -> int:
    if override is not None:
        if isinstance(override, int):
            return override
        return parse_level(str(override))

    env_level = os.environ.get(ENV_LOG_LEVEL)
    if env_level:
        return parse_level(env_level)

    section = load_mercury_section()
    configured = section.get("log_level")
    if configured:
        return parse_level(str(configured))

    return logging.INFO


def logging_enabled(*, override: bool | None = None) -> bool:
    if override is not None:
        return override

    env_value = env_bool(ENV_LOGGING_ENABLED)
    if env_value is not None:
        return env_value

    section = load_mercury_section()
    if "logging_enabled" in section:
        return bool(section["logging_enabled"])

    return True


def daily_log_path(*, log_dir: Path | None = None, moment: datetime | None = None) -> Path:
    directory = log_dir or resolve_log_dir()
    stamp = (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return directory / f"{MAIN_LOG_PREFIX}-{stamp}.log"


def error_log_path(*, log_dir: Path | None = None) -> Path:
    return (log_dir or resolve_log_dir()) / ERROR_LOG_FILENAME


def database_log_path(*, log_dir: Path | None = None) -> Path:
    return (log_dir or resolve_log_dir()) / DATABASE_LOG_FILENAME


def backup_log_path(*, log_dir: Path | None = None) -> Path:
    return (log_dir or resolve_log_dir()) / BACKUP_LOG_FILENAME


def normalize_logger_name(name: str) -> str:
    if name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}."):
        return name
    stripped = name.removeprefix("mercury.")
    return LOGGER_NAME if stripped == LOGGER_NAME else f"{LOGGER_NAME}.{stripped}"
