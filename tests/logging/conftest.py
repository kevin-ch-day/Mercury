"""Shared fixtures for Mercury logging tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mercury.logging import configure_logging, reset_logging


@pytest.fixture(autouse=True)
def _isolate_logging() -> Iterator[None]:
    reset_logging()
    yield
    reset_logging()


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def configured_logs(log_dir: Path) -> Path:
    configure_logging(enabled=True, log_dir=log_dir, level="DEBUG")
    return log_dir
