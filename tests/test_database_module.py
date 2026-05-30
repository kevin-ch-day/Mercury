"""Tests for mercury.database public API."""

import mercury.database as db
from mercury.database import discover, discover_demo


def test_public_api_exports() -> None:
    assert db.classify_database("erebus_threat_intel_prod").backup_source is True
    assert db.PLATFORM_DATABASES
    assert db.discover_demo
    assert db.load_mariadb_config
    assert db.print_inventory
    assert db.build_demo_backup_plan
    assert db.validate_config_policy
    assert db.backup_source_names
    assert db.DatabaseService
    assert db.default_service


def test_discover_config_mode() -> None:
    inv = discover("config")
    assert inv.mode == "config_and_catalog"


def test_database_package_layout() -> None:
    import mercury.database.core
    import mercury.database.discovery
    import mercury.database.mariadb

    assert hasattr(mercury.database.core, "classify_database")
    assert hasattr(mercury.database.discovery, "discover")
    assert hasattr(mercury.database.mariadb, "discover_databases_live")


def test_database_core_is_canonical() -> None:
    import mercury.database.core as core

    assert core.classify_database is db.classify_database
    assert core.DatabaseInventory is db.DatabaseInventory


def test_service_facade_demo_plan() -> None:
    plan = db.default_service.backup_plan_demo()
    assert plan.mode == "dry-run"
    assert "erebus_threat_intel_prod" in plan.backup_sources


def test_inventory_ops_backup_sources() -> None:
    inv = discover_demo()
    names = db.backup_source_names(inv)
    assert "android_permission_intel" in names
    assert all("_dev" not in n for n in names)
