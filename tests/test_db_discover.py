"""Tests for config-based database discovery."""

from pathlib import Path

import pytest

from mercury.database.core import PLATFORM_DATABASES, DatabaseInventory, DatabaseRole, classify_database, inventory_summary, load_databases_from_file
from mercury.database.discovery import discover_demo, discover_for_planning, discover_from_config
from mercury.backup.batch_runner import resolve_batch_sources
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
        "obsidiandroid_core_prod",
    }

# merged from test_discover_for_planning.py
def test_discover_for_planning_falls_back_to_demo_when_live_empty(monkeypatch) -> None:
    empty_live = DatabaseInventory(connection="connected", mode="mariadb_readonly", primary_config="local.toml")

    monkeypatch.setattr(
        "mercury.database.discovery.discover",
        lambda mode, **kwargs: empty_live if mode == "live" else (_ for _ in ()).throw(AssertionError(mode)),
    )
    monkeypatch.setattr(
        "mercury.database.mariadb.session.try_load_mariadb_config",
        lambda: object(),
    )

    inventory = discover_for_planning(live=True)
    assert inventory.count > 0
    sources = resolve_batch_sources(live=True)
    assert "erebus_threat_intel_prod" in sources


def test_resolve_batch_sources_live_anchors_configured_missing_sources(monkeypatch) -> None:
    from mercury.database.core import DatabaseInventory, record_from_name
    from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES
    from mercury.database.core.sources import SOURCE_LIVE

    inventory = DatabaseInventory(
        connection="connected",
        entries=[
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE, connected=True),
            record_from_name("android_permission_intel", SOURCE_LIVE, connected=True),
            record_from_name("scytaledroid_core_prod", SOURCE_LIVE, connected=True),
        ],
    )
    monkeypatch.setattr(
        "mercury.database.discovery.discover_for_planning",
        lambda live=False: inventory,
    )

    sources = resolve_batch_sources(live=True)
    assert len(sources) == len(ACTIVE_BACKUP_SOURCE_DATABASES)
    assert "obsidiandroid_core_prod" in sources
    assert "gecko_research_database_prod" not in sources

# merged from test_db_classifier.py
@pytest.mark.parametrize(
    ("name", "role", "backup_source", "dev_target", "manual_review"),
    [
        ("erebus_threat_intel_prod", DatabaseRole.PRODUCTION, True, False, False),
        ("scytaledroid_core_prod", DatabaseRole.PRODUCTION, True, False, False),
        ("obsidiandroid_core_prod", DatabaseRole.PRODUCTION, True, False, False),
        ("erebus_threat_intel_dev", DatabaseRole.DEVELOPMENT, False, True, False),
        ("gecko_research_database_dev", DatabaseRole.DEVELOPMENT, False, True, False),
        ("android_permission_intel", DatabaseRole.SHARED_AUTHORITY, True, False, False),
        (
            "_restorecheck_erebus_threat_intel_prod_20260530",
            DatabaseRole.RESTORE_CHECK_TEMP,
            False,
            False,
            False,
        ),
        ("random_test_db", DatabaseRole.UNKNOWN, False, False, True),
    ],
)
def test_classify_database(
    name: str,
    role: DatabaseRole,
    backup_source: bool,
    dev_target: bool,
    manual_review: bool,
) -> None:
    result = classify_database(name)
    assert result.role == role
    assert result.backup_source is backup_source
    assert result.dev_target is dev_target
    assert result.manual_review is manual_review

