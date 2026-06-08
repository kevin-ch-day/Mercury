"""Tests for config-based database discovery."""

from pathlib import Path

import pytest

from mercury.database.core import PLATFORM_DATABASES, inventory_summary, load_databases_from_file
from mercury.database.discovery import discover_demo, discover_from_config
from mercury.core.paths import DATABASES_EXAMPLE


def test_load_databases_example_toml() -> None:
    assert DATABASES_EXAMPLE.exists()
    names = load_databases_from_file(DATABASES_EXAMPLE)
    assert "erebus_threat_intel_prod" in names
    assert "erebus_threat_intel_dev" in names
    assert names["erebus_threat_intel_prod"]["host"] == "localhost"
    assert names["erebus_threat_intel_prod"]["port"] == 3306


def test_discover_demo_mode() -> None:
    inventory = discover_demo()
    assert inventory.mode == "demo"
    assert inventory.count >= len(PLATFORM_DATABASES)


def test_discover_includes_platform_catalog() -> None:
    inventory = discover_from_config(include_catalog=True)
    names = {e.name for e in inventory.entries}
    for db in PLATFORM_DATABASES:
        assert db in names
    assert inventory.connection == "not_connected"
    assert all(not e.connected for e in inventory.entries)


def test_discover_config_only_uses_toml() -> None:
    inventory = discover_from_config(include_catalog=False)
    names = {e.name for e in inventory.entries}
    assert "erebus_threat_intel_prod" in names
    assert names  # non-empty from example config in repo


def test_discover_merges_host_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    toml = config_dir / "databases.toml"
    toml.write_text(
        '[databases]\n'
        'custom_prod = { host = "db.example.com", port = 5433 }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("mercury.paths.DATABASES_LOCAL", toml)
    monkeypatch.setattr("mercury.paths.DATABASES_EXAMPLE", config_dir / "missing.toml")

    inventory = discover_from_config(include_catalog=False, prefer_local=True)
    assert len(inventory.entries) == 1
    entry = inventory.entries[0]
    assert entry.name == "custom_prod"
    assert entry.host == "db.example.com"
    assert entry.port == 5433
    assert entry.role == "production"
    assert entry.backup_source is True


def test_catalog_entries_have_project() -> None:
    inventory = discover_demo()
    erebus = next(e for e in inventory.entries if e.name == "erebus_threat_intel_prod")
    assert erebus.project == "Erebus"
    perm = next(e for e in inventory.entries if e.name == "android_permission_intel")
    assert perm.project == "Platform"


def test_inventory_summary_counts_roles() -> None:
    inventory = discover_from_config(include_catalog=True)
    summary = inventory_summary(inventory)
    assert summary.get("production", 0) >= 2
    assert summary.get("development", 0) >= 2


def test_demo_catalog_matches_real_platform_databases() -> None:
    names = set(PLATFORM_DATABASES)
    assert names == {
        "erebus_threat_intel_prod",
        "erebus_threat_intel_dev",
        "android_permission_intel",
        "scytaledroid_core_prod",
        "scytaledroid_core_dev",
    }
