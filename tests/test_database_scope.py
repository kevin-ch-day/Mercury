"""Tests for out-of-scope database filtering."""

from mercury.database.core import OUT_OF_SCOPE_DATABASES, is_in_scope
from mercury.database.core.models import DatabaseInventory
from mercury.database.core.scope import filter_inventory
from mercury.database.core.sources import SOURCE_LIVE
from mercury.database.core import record_from_name
from mercury.database.discovery import discover_demo
from mercury.database.prod_dev_pairs import build_prod_dev_pairs, orphan_dev_databases


def test_out_of_scope_names() -> None:
    assert "gecko_research_database_prod" in OUT_OF_SCOPE_DATABASES
    assert "gecko_research_database_dev" in OUT_OF_SCOPE_DATABASES
    assert "proofpoint_cti_db_dev" in OUT_OF_SCOPE_DATABASES
    assert "droid_threat_intel_db_dev" in OUT_OF_SCOPE_DATABASES
    assert "droid_threat_intel_db_prod" in OUT_OF_SCOPE_DATABASES
    assert is_in_scope("erebus_threat_intel_dev")
    assert not is_in_scope("proofpoint_cti_db_dev")
    assert not is_in_scope("droid_threat_intel_db_prod")


def test_discover_demo_excludes_out_of_scope() -> None:
    inventory = discover_demo()
    names = set(inventory.names)
    assert "gecko_research_database_prod" not in names
    assert "gecko_research_database_dev" not in names
    assert "proofpoint_cti_db_dev" not in names
    assert "droid_threat_intel_db_dev" not in names
    assert "droid_threat_intel_db_prod" not in names


def test_live_discovery_keeps_out_of_scope_databases_visible(monkeypatch) -> None:
    from mercury.database.discovery import discover
    from mercury.database.core.models import DatabaseInventory
    from mercury.database.core import record_from_name
    from mercury.database.core.sources import SOURCE_LIVE

    def fake_discover(_cfg, connect_fn=None):
        return DatabaseInventory(
            mode="live",
            connection="ok",
            entries=[
                record_from_name("erebus_threat_intel_prod", SOURCE_LIVE, connected=True),
                record_from_name("droid_threat_intel_db_prod", SOURCE_LIVE, connected=True),
                record_from_name("proofpoint_cti_db_dev", SOURCE_LIVE, connected=True),
            ],
        )

    monkeypatch.setattr("mercury.database.mariadb.discover_databases_live", fake_discover)
    monkeypatch.setattr("mercury.database.mariadb.load_mariadb_config", lambda: object())
    inventory = discover("live")
    names = {entry.name for entry in inventory.entries}
    assert "erebus_threat_intel_prod" in names
    assert "droid_threat_intel_db_prod" in names
    assert "proofpoint_cti_db_dev" in names
    droid = next(entry for entry in inventory.entries if entry.name == "droid_threat_intel_db_prod")
    proofpoint = next(entry for entry in inventory.entries if entry.name == "proofpoint_cti_db_dev")
    assert droid.backup_source is False
    assert droid.dev_target is False
    assert proofpoint.backup_source is False
    assert proofpoint.dev_target is False


def test_filter_inventory_drops_out_of_scope() -> None:
    inventory = DatabaseInventory(
        entries=[
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE),
            record_from_name("proofpoint_cti_db_dev", SOURCE_LIVE),
        ]
    )
    filtered = filter_inventory(inventory)
    assert [entry.name for entry in filtered.entries] == ["erebus_threat_intel_prod"]


def test_prod_dev_pairs_skip_out_of_scope_dev_targets() -> None:
    names = [
        "droid_threat_intel_db_prod",
        "droid_threat_intel_db_dev",
        "erebus_threat_intel_prod",
        "erebus_threat_intel_dev",
    ]
    pairs = build_prod_dev_pairs(names)
    prod_names = {pair.prod for pair in pairs}
    assert "erebus_threat_intel_prod" in prod_names
    assert "droid_threat_intel_db_prod" not in prod_names


def test_orphan_dev_excludes_out_of_scope() -> None:
    names = ["proofpoint_cti_db_dev", "erebus_threat_intel_dev"]
    orphans = orphan_dev_databases(names, [])
    assert orphans == ["erebus_threat_intel_dev"]
