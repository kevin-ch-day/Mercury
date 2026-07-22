"""MariaDB session, config, discovery, and probe (mocked unit tests)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.database import (
    MariaDbConfigError,
    MariaDbConnectionConfig,
    build_readonly_discovery_plan,
    discover_demo,
    discover_databases_live,
    fetch_database_names,
    load_mariadb_config,
    probe_client_tooling,
    probe_mariadb_server,
    resolve_mariadb_target,
    try_load_mariadb_config,
)
from mercury.core.safety import LIVE_ACTIONS_ENABLED, MODE_SEED
from mercury.database.mariadb.session import SYSTEM_DATABASES, MariaDbServerProbe, _filter_user_databases
from mercury.database.terminal.ping import print_server_probe
from tests.conftest import run_cli, subprocess_env


def test_filter_user_databases_excludes_system() -> None:
    names = _filter_user_databases(
        ["information_schema", "erebus_threat_intel_prod", "mysql", "mysql", "sys"]
    )
    assert names == ["erebus_threat_intel_prod"]


def test_fetch_database_names_uses_connect_fn() -> None:
    config = MariaDbConnectionConfig(
        host="db.local",
        port=3306,
        user="reader",
        password="secret",
    )

    def fake_connect(_cfg: MariaDbConnectionConfig) -> list[str]:
        return ["mysql", "erebus_threat_intel_prod", "erebus_threat_intel_dev"]

    names = fetch_database_names(config, connect_fn=fake_connect)
    assert "mysql" not in names
    assert "erebus_threat_intel_prod" in names
    assert "erebus_threat_intel_dev" in names


def test_discover_databases_live_classifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="reader",
        password="secret",
    )

    def fake_connect(_cfg: MariaDbConnectionConfig) -> list[str]:
        return [
            "erebus_threat_intel_prod",
            "erebus_threat_intel_dev",
            "android_permission_intel",
            "_restorecheck_test",
        ]

    inventory = discover_databases_live(config, connect_fn=fake_connect)
    assert inventory.mode == "mariadb_readonly"
    assert inventory.connection.startswith("connected")
    assert inventory.entries[0].connected is True
    assert all(e.config_source == "mariadb:live" for e in inventory.entries)

    by_name = {e.name: e for e in inventory.entries}
    assert by_name["erebus_threat_intel_prod"].backup_source is True
    assert by_name["erebus_threat_intel_dev"].backup_source is False
    assert by_name["android_permission_intel"].backup_source is True


def test_load_mariadb_config_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = tmp_path / "local.toml"
    local.write_text(
        """
[mariadb]
host = "mariadb.example.com"
port = 3307
user = "mercury"
password_env = "MERCURY_MARIADB_PASSWORD"
connect_timeout = 5
ssl_disabled = true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MERCURY_MARIADB_PASSWORD", "s3cret")
    cfg = load_mariadb_config(local)
    assert cfg.host == "mariadb.example.com"
    assert cfg.port == 3307
    assert cfg.user == "mercury"
    assert cfg.password == "s3cret"
    assert cfg.connect_timeout == 5


def test_load_mariadb_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MariaDbConfigError, match="not found"):
        load_mariadb_config(tmp_path / "missing.toml")


def test_load_mariadb_config_missing_password_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local.toml"
    local.write_text(
        '[mariadb]\nhost="h"\nuser="u"\npassword_env="MERCURY_MARIADB_PASSWORD"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MERCURY_MARIADB_PASSWORD", raising=False)
    with pytest.raises(MariaDbConfigError, match="not set"):
        load_mariadb_config(local)


def test_load_mariadb_config_missing_section(tmp_path: Path) -> None:
    local = tmp_path / "local.toml"
    local.write_text("[mercury]\nmode = 'seed'\n", encoding="utf-8")
    with pytest.raises(MariaDbConfigError, match="\\[mariadb\\]"):
        load_mariadb_config(local)


def test_live_and_demo_inventory_same_display_fields(
    tmp_path: Path,
) -> None:
    """Live records use same shape as demo for print_inventory."""
    config = MariaDbConnectionConfig(host="h", port=1, user="u", password="p")

    def fake_connect(_cfg: MariaDbConnectionConfig) -> list[str]:
        return ["erebus_threat_intel_prod"]

    live = discover_databases_live(config, connect_fn=fake_connect)
    demo = discover_demo()
    live_entry = live.entries[0]
    assert live_entry.name
    assert live_entry.role
    assert hasattr(live_entry, "backup_source")
    assert demo.entries[0].name  # demo has catalog entries


def test_cli_discover_without_config_fails(tmp_path: Path) -> None:
    empty_config = tmp_path / "config"
    empty_config.mkdir()
    absent = empty_config / "local.toml"

    env = subprocess_env({"MERCURY_LOCAL_CONFIG": str(absent)})
    env.pop("MERCURY_MARIADB_PASSWORD", None)

    result = run_cli("db", "discover", cwd=tmp_path, env=env)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "demo" in combined.lower() or "local.toml" in combined.lower()


def test_system_databases_constant() -> None:
    assert "information_schema" in SYSTEM_DATABASES
    assert "performance_schema" in SYSTEM_DATABASES


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
    from tests.conftest import run_cli, subprocess_env

    absent = "/tmp/mercury-pytest-absent-local.toml"
    result = run_cli("db", "ping", env=subprocess_env({"MERCURY_LOCAL_CONFIG": absent}))
    assert result.returncode != 0
    assert "local.toml" in (result.stdout + result.stderr).lower()


def test_probe_requires_config_when_no_config_passed(tmp_path: Path) -> None:
    with pytest.raises(MariaDbConfigError):
        probe_mariadb_server(config_path=tmp_path / "missing.toml")

# merged from test_mariadb_probe.py
def test_probe_client_tooling_returns_all_tools() -> None:
    tooling = probe_client_tooling()
    assert tooling.platform
    assert "mariadb" in tooling.tools
    assert "mariadb-dump" in tooling.tools

# merged from test_mariadb_probe.py
def test_readonly_plan_is_not_executed_in_seed() -> None:
    plan = build_readonly_discovery_plan()
    assert plan.mode == MODE_SEED
    assert plan.live_actions_enabled is LIVE_ACTIONS_ENABLED
    assert "SHOW DATABASES" in plan.planned_sql[0]
    assert plan.status in (
        "seed_disabled",
        "live_disabled",
        "ready_not_executed",
        "implemented",
    )

