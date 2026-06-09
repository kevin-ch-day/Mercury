"""Tests for restore-check interactive menu."""

from __future__ import annotations

import pytest

from mercury.restore.interactive_menu import run_restore_menu


def test_run_restore_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_restore_menu(interactive=False)
    out = capsys.readouterr().out
    assert "Restore-check Operations" in out
    assert "Ready sources" in out
    assert "Blocked sources" in out
    assert "Plan mode" in out
    assert "DATABASE" in out
    assert "STATUS" in out
    assert "\n      [1] Refresh" in out
    assert "Run restore-checks" in out
    assert "[0] Back" in out
