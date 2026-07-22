"""Live MariaDB access via client mode and platform checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.database.mariadb.access import build_platform_access_report
from mercury.database.mariadb.client import client_fetch_scalar, client_fetch_scalars
from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.session import fetch_user_database_names, probe_mariadb_server

from tests.conftest import (
    DEFAULT_MARIADB_SOCKET,
    live_mariadb_client_config,
    mariadb_client_connects,
    platform_prod_databases_present,
    repo_local_config,
    run_cli,
    subprocess_env,
)

LOCAL_CONFIG = repo_local_config()
SOCKET_PATH = DEFAULT_MARIADB_SOCKET


def _client_config() -> MariaDbConnectionConfig:
    return live_mariadb_client_config()


def _live_mariadb_available() -> bool:
    return LOCAL_CONFIG.exists() and mariadb_client_connects(SOCKET_PATH)


def _live_cli_env() -> dict[str, str]:
    """Subprocess env that still sees the operator local.toml for live DB CLI tests."""
    return subprocess_env({"MERCURY_LOCAL_CONFIG": str(LOCAL_CONFIG)})


@pytest.mark.skipif(not mariadb_client_connects(SOCKET_PATH), reason="MariaDB client connection unavailable")
class TestMariaDbClientIntegration:
    def test_client_fetch_scalar_version(self) -> None:
        version = client_fetch_scalar(_client_config(), "SELECT VERSION()")
        assert "MariaDB" in version or "mysql" in version.lower()

    def test_client_show_databases(self) -> None:
        names = client_fetch_scalars(_client_config(), "SHOW DATABASES")
        assert "information_schema" in names

    def test_probe_via_client_config(self) -> None:
        probe = probe_mariadb_server(_client_config())
        assert probe.connected is True
        assert probe.driver == "client"
        assert probe.user_database_count is not None
        assert probe.user_database_count >= 0

    @pytest.mark.skipif(
        not platform_prod_databases_present(),
        reason="Platform prod databases not present on MariaDB server",
    )
    def test_fetch_user_database_names(self) -> None:
        names = fetch_user_database_names(_client_config())
        assert "erebus_threat_intel_prod" in names

    @pytest.mark.skipif(
        not platform_prod_databases_present(),
        reason="Platform prod databases not present on MariaDB server",
    )
    def test_inspect_prod_database(self) -> None:
        result = inspect_database_on_server("erebus_threat_intel_prod", _client_config())
        assert result.exists_on_server is True
        assert result.table_count is not None
        assert result.table_count >= 0

    @pytest.mark.skipif(
        not platform_prod_databases_present(),
        reason="Platform prod databases not present on MariaDB server",
    )
    def test_platform_access_report(self) -> None:
        report = build_platform_access_report(_client_config())
        assert report.server_database_count >= 1
        assert "erebus_threat_intel_prod" in report.present


def test_load_config_use_client_optional_password(tmp_path: Path) -> None:
    local = tmp_path / "local.toml"
    local.write_text(
        """
[mariadb]
host = "127.0.0.1"
port = 3306
user = "root"
use_client = true
unix_socket = "/var/lib/mysql/mysql.sock"
""".strip(),
        encoding="utf-8",
    )
    cfg = load_mariadb_config(local)
    assert cfg.use_client is True
    assert cfg.unix_socket == "/var/lib/mysql/mysql.sock"
    assert cfg.password == ""


def test_cli_db_ping_with_local_config() -> None:
    if not _live_mariadb_available():
        pytest.skip("local config or MariaDB socket unavailable")
    result = run_cli("db", "ping", env=_live_cli_env())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "connected" in result.stdout.lower()
    assert "MariaDB" in result.stdout or "mariadb" in result.stdout.lower()


def test_cli_db_discover_live() -> None:
    if not _live_mariadb_available():
        pytest.skip("local config or MariaDB connection unavailable")
    if not platform_prod_databases_present():
        pytest.skip("Platform prod databases not present on MariaDB server")
    result = run_cli("db", "discover", env=_live_cli_env())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "erebus_threat_intel_prod" in result.stdout


def test_cli_db_access() -> None:
    if not _live_mariadb_available():
        pytest.skip("local config or MariaDB socket unavailable")
    result = run_cli("db", "access", env=_live_cli_env())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLATFORM DATABASE ACCESS" in result.stdout or "Platform database access" in result.stdout
