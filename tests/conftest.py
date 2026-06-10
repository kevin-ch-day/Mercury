"""Shared pytest fixtures and helpers for the Mercury test suite."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "mercury.cli"]
ENV_STATE_ROOT = "MERCURY_STATE_ROOT"

FIXED_DATE = "2026-05-30"
FIXED_TS = "20260530_120000"
FIXED_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)

DEFAULT_MARIADB_SOCKET = Path("/var/lib/mysql/mysql.sock")


def live_mariadb_client_config():
    """MariaDB client config for integration tests (local.toml when present)."""
    from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config

    local = repo_local_config()
    if local.exists():
        try:
            return load_mariadb_config(local)
        except Exception:
            pass
    return MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket=str(DEFAULT_MARIADB_SOCKET),
    )


def mariadb_client_connects(path: Path = DEFAULT_MARIADB_SOCKET) -> bool:
    if not mariadb_socket_available(path):
        return False
    from mercury.database.mariadb.client import client_fetch_scalar
    from mercury.database.mariadb.errors import MariaDbLiveError

    try:
        client_fetch_scalar(live_mariadb_client_config(), "SELECT 1")
    except MariaDbLiveError:
        return False
    return True


def platform_prod_databases_present() -> bool:
    if not mariadb_client_connects():
        return False
    from mercury.database.mariadb.session import fetch_user_database_names

    try:
        names = fetch_user_database_names(live_mariadb_client_config())
    except Exception:
        return False
    return "erebus_threat_intel_prod" in names


def run_cli(
    *args: str,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``mercury.cli`` in a subprocess and return the completed process."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=merged_env,
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


@pytest.fixture(autouse=True)
def _isolate_mercury_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep pytest ledger writes off the operator USB state tree."""
    state_root = tmp_path / "mercury_state"
    state_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(ENV_STATE_ROOT, str(state_root))
    yield
