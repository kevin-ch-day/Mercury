"""Tests for out-of-scope database filtering."""

import pytest

from mercury.database.core import OUT_OF_SCOPE_DATABASES, is_in_scope
from mercury.database.core.models import DatabaseInventory
from mercury.database.core.scope import filter_inventory
from mercury.database.core.sources import SOURCE_LIVE
from mercury.database.core import record_from_name
from mercury.database.discovery import discover_demo
from mercury.database.mariadb.active_scope import fetch_active_scope_report
from mercury.database.prod_dev_pairs import build_prod_dev_pairs, orphan_dev_databases
from mercury.database.terminal.active_scope import print_active_scope_report


def test_out_of_scope_names() -> None:
    assert "android_permission_intel_prod" in OUT_OF_SCOPE_DATABASES
    assert "android_permission_intel_dev" in OUT_OF_SCOPE_DATABASES
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
    assert "android_permission_intel_prod" not in names
    assert "android_permission_intel_dev" not in names
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
                record_from_name("android_permission_intel_prod", SOURCE_LIVE, connected=True),
                record_from_name("android_permission_intel_dev", SOURCE_LIVE, connected=True),
                record_from_name("droid_threat_intel_db_prod", SOURCE_LIVE, connected=True),
                record_from_name("proofpoint_cti_db_dev", SOURCE_LIVE, connected=True),
            ],
        )

    monkeypatch.setattr("mercury.database.mariadb.discover_databases_live", fake_discover)
    monkeypatch.setattr("mercury.database.mariadb.load_mariadb_config", lambda: object())
    inventory = discover("live")
    names = {entry.name for entry in inventory.entries}
    assert "erebus_threat_intel_prod" in names
    assert "android_permission_intel_prod" in names
    assert "android_permission_intel_dev" in names
    assert "droid_threat_intel_db_prod" in names
    assert "proofpoint_cti_db_dev" in names
    android_prod = next(entry for entry in inventory.entries if entry.name == "android_permission_intel_prod")
    android_dev = next(entry for entry in inventory.entries if entry.name == "android_permission_intel_dev")
    droid = next(entry for entry in inventory.entries if entry.name == "droid_threat_intel_db_prod")
    proofpoint = next(entry for entry in inventory.entries if entry.name == "proofpoint_cti_db_dev")
    assert android_prod.backup_source is False
    assert android_prod.dev_target is False
    assert android_dev.backup_source is False
    assert android_dev.dev_target is False
    assert droid.backup_source is False
    assert droid.dev_target is False
    assert proofpoint.backup_source is False
    assert proofpoint.dev_target is False


def test_filter_inventory_drops_out_of_scope() -> None:
    inventory = DatabaseInventory(
        entries=[
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE),
            record_from_name("android_permission_intel_prod", SOURCE_LIVE),
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


class _ActiveScopeConfig:
    use_client = True


# merged from test_db_active_scope.py
def test_fetch_active_scope_report_uses_one_query() -> None:
    calls: list[str] = []

    def rows_fn(_config, sql: str) -> list[list[str]]:
        calls.append(sql)
        return [
            ["android_permission_intel", "1", "41", "35", "309088780"],
            ["erebus_threat_intel_dev", "1", "10", "0", "2048"],
            ["erebus_threat_intel_prod", "1", "12", "1", "4096"],
            ["scytaledroid_core_dev", "0", "0", "0", "0"],
            ["scytaledroid_core_prod", "1", "22", "2", "8192"],
        ]

    report = fetch_active_scope_report(_ActiveScopeConfig(), rows_fn=rows_fn)
    assert len(calls) == 1
    assert "information_schema.schemata" in calls[0]
    assert report.database_count == 5
    assert report.present_count == 4
    assert report.missing_count == 1
    android = next(row for row in report.rows if row.name == "android_permission_intel")
    assert android.sync_role == "backup-only"
    prod = next(row for row in report.rows if row.name == "erebus_threat_intel_prod")
    assert prod.sync_role == "source+pair"
    dev = next(row for row in report.rows if row.name == "erebus_threat_intel_dev")
    assert dev.sync_role == "dev target"

# merged from test_db_active_scope.py
def test_print_active_scope_report_compact(capsys: pytest.CaptureFixture[str]) -> None:
    report = fetch_active_scope_report(
        _ActiveScopeConfig(),
        rows_fn=lambda _config, _sql: [
            ["android_permission_intel", "1", "41", "35", "309088780"],
            ["erebus_threat_intel_dev", "1", "10", "0", "2048"],
            ["erebus_threat_intel_prod", "1", "12", "1", "4096"],
            ["scytaledroid_core_dev", "0", "0", "0", "0"],
            ["scytaledroid_core_prod", "1", "22", "2", "8192"],
        ],
    )
    print_active_scope_report(report, compact=True)
    out = capsys.readouterr().out
    assert "Access mode:" in out
    assert "Present:" in out
    assert "DATABASE" in out
    assert "STATUS" in out
    assert "SYNC ROLE" in out
    assert "android_permission_intel" in out
    assert "backup-only" in out
    assert "source+pair" in out
    assert "dev target" in out

