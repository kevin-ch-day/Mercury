"""M6: read-only MariaDB server probe and connectivity."""

from pathlib import Path

import pytest

from mercury.database import (
    MariaDbConfigError,
    MariaDbConnectionConfig,
    probe_mariadb_server,
    resolve_mariadb_target,
    try_load_mariadb_config,
)
from mercury.database.display_ping import print_server_probe
from mercury.database.mariadb.session import MariaDbServerProbe


def test_try_load_mariadb_config_missing() -> None:
    assert try_load_mariadb_config(Path("/nonexistent/local.toml")) is None


def test_resolve_mariadb_target_offline() -> None:
    cfg = MariaDbConnectionConfig(
        host="db.example.com",
        port=3307,
        user="mercury_readonly",
        password="secret",
    )
    assert resolve_mariadb_target(cfg) == ("db.example.com", 3307, "mercury_readonly")


def test_probe_mariadb_server_with_probe_fn() -> None:
    config = MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="reader",
        password="secret",
    )

    def fake_probe(_cfg: MariaDbConnectionConfig) -> MariaDbServerProbe:
        return MariaDbServerProbe(
            host=_cfg.host,
            port=_cfg.port,
            configured_user=_cfg.user,
            connected=True,
            latency_ms=1.5,
            server_version="11.4.2-MariaDB",
            current_user="reader@localhost",
            user_database_count=4,
            sample_databases=["erebus_threat_intel_prod"],
            sql_executed=["SELECT 1 AS ok", "SHOW DATABASES"],
        )

    probe = probe_mariadb_server(config, probe_fn=fake_probe)
    assert probe.connected is True
    assert probe.server_version == "11.4.2-MariaDB"
    assert probe.user_database_count == 4
    assert probe.read_only is True


def test_probe_display_includes_version(capsys: pytest.CaptureFixture[str]) -> None:
    probe = MariaDbServerProbe(
        host="127.0.0.1",
        port=3306,
        configured_user="reader",
        connected=True,
        server_version="11.4.2-MariaDB",
        current_user="reader@127.0.0.1",
        user_database_count=2,
        sample_databases=["erebus_threat_intel_prod"],
    )
    print_server_probe(probe)
    out = capsys.readouterr().out
    assert "MariaDB server probe" in out
    assert "11.4.2-MariaDB" in out
    assert "read-only" in out.lower()


def test_cli_db_ping_without_config() -> None:
    import subprocess
    import sys
    from pathlib import Path

    repo_local = Path(__file__).resolve().parents[1] / "config" / "local.toml"
    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "db", "ping"],
        capture_output=True,
        text=True,
    )
    if repo_local.exists():
        assert result.returncode == 0
        assert "MariaDB server probe" in result.stdout
    else:
        assert result.returncode != 0
        assert "local.toml" in (result.stdout + result.stderr).lower()


def test_probe_requires_config_when_no_config_passed(tmp_path: Path) -> None:
    with pytest.raises(MariaDbConfigError):
        probe_mariadb_server(config_path=tmp_path / "missing.toml")
