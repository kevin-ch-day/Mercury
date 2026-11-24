"""Shared fixtures for Mercury logging tests."""

from __future__ import annotations

from pathlib import Path

import pytest

@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path
