"""Tests for compact database inventory display."""

from __future__ import annotations

import pytest

from mercury.database.core import record_from_name, role_env_label
from mercury.database.core.sources import SOURCE_LIVE
from mercury.database.discovery import discover_demo
from mercury.database.terminal.inventory import print_inventory


def test_role_env_label_maps_naming_convention() -> None:
    assert role_env_label("production") == "PROD"
    assert role_env_label("development") == "DEV"
    assert role_env_label("shared_authority") == "SHARED"


def test_print_inventory_compact_table(capsys: pytest.CaptureFixture[str]) -> None:
    inventory = discover_demo()
    print_inventory(inventory, compact=True)
    out = capsys.readouterr().out
    assert "DATABASE" in out
    assert "ENV" in out
    assert "BACKUP" in out
    assert "PROD" in out
    assert "DEV" in out
    assert "erebus_threat_intel_prod" in out
    assert "backup_source" not in out
    assert "127.0.0.1" not in out


def test_prod_before_dev_in_table(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.database.core.models import DatabaseInventory

    inventory = DatabaseInventory(
        connection="test",
        entries=[
            record_from_name("erebus_threat_intel_dev", SOURCE_LIVE),
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE),
        ],
    )
    print_inventory(inventory, compact=True)
    out = capsys.readouterr().out
    assert out.index("erebus_threat_intel_prod") < out.index("erebus_threat_intel_dev")
