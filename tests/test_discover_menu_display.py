"""Tests for compact menu database discovery display."""

from __future__ import annotations

import pytest

from mercury.database.core.models import DatabaseInventory
from mercury.database.core.sources import SOURCE_LIVE
from mercury.database.core import record_from_name
from mercury.database.discovery import discover_demo
from mercury.database.terminal.discover_menu import (
    build_discover_menu_fields,
    print_discover_menu,
)


def test_build_discover_menu_fields_includes_roles() -> None:
    inventory = discover_demo()
    fields = build_discover_menu_fields(inventory)
    assert fields["Active scope"] == inventory.count
    assert fields["Backup sources"] == 3
    assert fields["Sync targets"] == 2


def test_build_discover_menu_fields_total_size() -> None:
    inventory = DatabaseInventory(
        connection="test",
        entries=[record_from_name("erebus_threat_intel_prod", SOURCE_LIVE)],
    )
    fields = build_discover_menu_fields(
        inventory,
        size_by_name={"erebus_threat_intel_prod": 1024 * 1024},
    )
    assert fields["Total size"] == "1.00 MiB"


def test_print_discover_menu_compact_table(capsys: pytest.CaptureFixture[str]) -> None:
    print_discover_menu(discover_demo())
    out = capsys.readouterr().out
    assert "Active scope:" in out
    assert "Backup sources:" in out
    assert "Sync targets:" in out
    assert "DATABASE                      ROLE      BACKUP    SYNC ROLE" in out
    assert "android_permission_intel      SHARED    yes       backup-only" in out
    assert "DATABASE" in out
    assert "ROLE" in out
    assert "BACKUP" in out
    assert "SYNC ROLE" in out
    assert "PROD" in out
    assert "android_permission_intel" in out
    assert "backup-only" in out
    assert "SIZE" not in out
    assert "PROJECT" not in out
    assert "on server" not in out


def test_prod_before_dev_in_menu_table(capsys: pytest.CaptureFixture[str]) -> None:
    inventory = DatabaseInventory(
        connection="test",
        entries=[
            record_from_name("erebus_threat_intel_dev", SOURCE_LIVE),
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE),
        ],
    )
    print_discover_menu(inventory)
    out = capsys.readouterr().out
    assert out.index("erebus_threat_intel_prod") < out.index("erebus_threat_intel_dev")


def test_out_of_scope_databases_are_excluded_from_primary_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = DatabaseInventory(
        connection="test",
        entries=[
            record_from_name("android_permission_intel_prod", SOURCE_LIVE),
            record_from_name("android_permission_intel_dev", SOURCE_LIVE),
            record_from_name("droid_threat_intel_db_prod", SOURCE_LIVE),
            record_from_name("proofpoint_cti_db_dev", SOURCE_LIVE),
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE),
        ],
    )
    print_discover_menu(inventory)
    out = capsys.readouterr().out
    assert "Active scope: 1" in out
    assert "Backup sources: 1" in out
    assert "Sync targets: 0" in out
    assert "Out of scope: 4 ignored" in out
    assert "erebus_threat_intel_prod" in out
    assert "android_permission_intel_prod" in out
    assert "android_permission_intel_dev" in out
    assert "droid_threat_intel_db_prod" in out
    assert "proofpoint_cti_db_dev" in out
    assert "Ignored databases: 4" in out


def test_discover_menu_explains_shared_authority_source(capsys: pytest.CaptureFixture[str]) -> None:
    print_discover_menu(discover_demo())
    out = capsys.readouterr().out
    assert "Shared authority: android_permission_intel (backup-only)" in out


def test_run_discover_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.database.discovery_menu import run_discover_menu

    run_discover_menu(interactive=False)
    out = capsys.readouterr().out
    assert "Active scope:" in out
    assert "Rescan inventory" in out
    assert "CLI:" not in out
