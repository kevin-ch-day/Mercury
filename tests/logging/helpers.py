"""Helpers for logging tests (not fixtures — see conftest.py)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from mercury.logging import current_log_file

LOGGER_NAMES = ("mercury", "mercury.database", "mercury.backup")


def flush_logs(logger_names: tuple[str, ...] = LOGGER_NAMES) -> None:
    for name in logger_names:
        for handler in logging.getLogger(name).handlers:
            handler.flush()


def read_log(path_getter: Callable[[], Path | None]) -> str:
    path = path_getter()
    assert path is not None, "expected log file to exist"
    flush_logs()
    return path.read_text(encoding="utf-8")


def read_main_log() -> str:
    return read_log(current_log_file)
