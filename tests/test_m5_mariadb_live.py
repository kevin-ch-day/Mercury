"""M5: live read-only MariaDB discovery (mocked)."""

from pathlib import Path

import pytest

from mercury.database import (
    MariaDbConfigError,
    MariaDbConnectionConfig,
    discover_demo,
    discover_databases_live,
    fetch_database_names,
    load_mariadb_config,
)
from mercury.database.mariadb.live import SYSTEM_DATABASES, _filter_user_databases


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


def test_cli_discover_without_config_fails() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "mercury.cli", "db", "discover"],
        capture_output=True,
        text=True,
        env={k: v for k, v in __import__("os").environ.items() if k != "MERCURY_MARIADB_PASSWORD"},
    )
    # May fail on config or connection; without local.toml in CI should mention config
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "demo" in combined.lower() or "local.toml" in combined.lower()


def test_system_databases_constant() -> None:
    assert "information_schema" in SYSTEM_DATABASES
    assert "performance_schema" in SYSTEM_DATABASES
