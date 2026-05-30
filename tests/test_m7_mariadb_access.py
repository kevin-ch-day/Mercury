"""Live MariaDB access via client mode and platform checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mercury.database.mariadb.access import build_platform_access_report
from mercury.database.mariadb.client import client_fetch_scalar, client_fetch_scalars
from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.session import fetch_user_database_names, probe_mariadb_server

LOCAL_CONFIG = Path(__file__).resolve().parents[1] / "config" / "local.toml"
SOCKET_PATH = Path("/var/lib/mysql/mysql.sock")


def _client_config() -> MariaDbConnectionConfig:
    return MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket=str(SOCKET_PATH),
    )


@pytest.mark.skipif(not SOCKET_PATH.exists(), reason="MariaDB socket not present")
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
        assert probe.user_database_count >= 1

    def test_fetch_user_database_names(self) -> None:
        names = fetch_user_database_names(_client_config())
        assert "erebus_threat_intel_prod" in names

    def test_inspect_prod_database(self) -> None:
        result = inspect_database_on_server("erebus_threat_intel_prod", _client_config())
        assert result.exists_on_server is True
        assert result.table_count is not None
        assert result.table_count >= 0

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
    if not LOCAL_CONFIG.exists() or not SOCKET_PATH.exists():
        pytest.skip("local config or MariaDB socket unavailable")
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "db", "ping"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "connected" in result.stdout.lower()
    assert "MariaDB" in result.stdout or "mariadb" in result.stdout.lower()


def test_cli_db_discover_live() -> None:
    if not LOCAL_CONFIG.exists() or not SOCKET_PATH.exists():
        pytest.skip("local config or MariaDB socket unavailable")
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "db", "discover"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "erebus_threat_intel_prod" in result.stdout
    assert "Live read-only discovery" in result.stdout


def test_cli_db_access() -> None:
    if not LOCAL_CONFIG.exists() or not SOCKET_PATH.exists():
        pytest.skip("local config or MariaDB socket unavailable")
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "db", "access"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLATFORM DATABASE ACCESS" in result.stdout
