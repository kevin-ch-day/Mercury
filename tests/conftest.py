"""Shared pytest fixtures and helpers for the Mercury test suite."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "mercury.cli"]

FIXED_DATE = "2026-05-30"
FIXED_TS = "20260530_120000"
FIXED_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)

DEFAULT_MARIADB_SOCKET = Path("/var/lib/mysql/mysql.sock")


def run_cli(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``mercury.cli`` in a subprocess and return the completed process."""
    return subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def mariadb_socket_available(path: Path = DEFAULT_MARIADB_SOCKET) -> bool:
    try:
        return path.is_socket() or path.exists()
    except OSError:
        return False


def repo_local_config() -> Path:
    return REPO_ROOT / "config" / "local.toml"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(autouse=True)
def _reset_menu_prompt_reader() -> Iterator[None]:
    from mercury.menu.prompts import set_continue_reader, set_prompt_reader

    set_prompt_reader(None)
    set_continue_reader(lambda: None)
    yield
    set_prompt_reader(None)
    set_continue_reader(None)
