"""Tests for compact environment check display."""

from mercury.database.mariadb.session import MariaDbServerProbe
from mercury.env.terminal.check import build_environment_check_fields, connection_label
from mercury.env.probe import EnvProbeResult


def _env_result() -> EnvProbeResult:
    return EnvProbeResult(
        python_version="3.14.5",
        platform_system="Linux",
        platform_release="7.0.10",
        repo_root="/tmp",
        config_dir="/tmp/config",
        output_dir="/tmp/out",
        mode="seed",
        dry_run_only=True,
    )


def test_connection_label_socket() -> None:
    probe = MariaDbServerProbe(
        host="127.0.0.1",
        port=3306,
        configured_user="root",
        connected=True,
        current_user="root@localhost",
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    assert connection_label(probe) == "root@localhost"


def test_build_environment_check_fields_connected() -> None:
    probe = MariaDbServerProbe(
        host="localhost",
        port=3306,
        configured_user="root",
        connected=True,
        server_version="10.11.16-MariaDB",
        latency_ms=13.47,
        user_database_count=7,
        current_user="root@localhost",
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    fields = build_environment_check_fields(_env_result(), probe)
    assert fields["Runtime"]["Python"] == "3.14.5"
    assert fields["MariaDB"]["Status"] == "connected"
    assert fields["MariaDB"]["User"] == "root@localhost"
    assert fields["MariaDB"]["Socket"] == "/var/lib/mysql/mysql.sock"
    assert fields["Execution Safety"]["Mode"] == "DRY RUN"
    assert "Database Scope" not in fields
    assert "Recommended action" not in fields


def test_build_environment_check_fields_not_configured() -> None:
    fields = build_environment_check_fields(_env_result())
    assert "not configured" in str(fields["MariaDB"]["Status"])
