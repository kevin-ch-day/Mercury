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
    assert fields["databases"] == inventory.count
    assert "prod" in str(fields["roles"]).lower()


def test_build_discover_menu_fields_total_size() -> None:
    inventory = DatabaseInventory(
        connection="test",
        entries=[record_from_name("erebus_threat_intel_prod", SOURCE_LIVE)],
    )
    fields = build_discover_menu_fields(
        inventory,
        size_by_name={"erebus_threat_intel_prod": 1024 * 1024},
    )
    assert fields["total_size"] == "1.00 MiB"


def test_print_discover_menu_compact_table(capsys: pytest.CaptureFixture[str]) -> None:
    print_discover_menu(discover_demo())
    out = capsys.readouterr().out
    assert "databases:" in out
    assert "roles:" in out
    assert "DATABASE" in out
    assert "ENV" in out
    assert "BACKUP" in out
    assert "PROD" in out
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
