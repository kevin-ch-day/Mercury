"""Tests for interactive discover menu."""

from __future__ import annotations

import pytest

from mercury.database.discovery_menu import run_discover_menu


def test_run_discover_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_discover_menu(interactive=False)
    out = capsys.readouterr().out
    assert "databases:" in out
    assert "Rescan inventory" in out
    assert "CLI:" not in out
