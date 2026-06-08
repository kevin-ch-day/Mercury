"""Tests for verify interactive menu."""

from __future__ import annotations

import pytest

from mercury.verify.interactive_menu import run_verify_menu


def test_run_verify_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_verify_menu(interactive=False)
    out = capsys.readouterr().out
    assert "verified" in out
    assert "Rescan" in out
    assert "Verify all" in out
    assert "SOURCE ROLE" in out
    assert "shared authority source" in out
    assert "Actions" not in out
