"""Tests for interactive environment menu."""

from __future__ import annotations

import pytest

from mercury.env.interactive_menu import run_env_menu


def test_run_env_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_env_menu(interactive=False)
    out = capsys.readouterr().out
    assert "python:" in out
    assert "Rescan" in out
    assert "Live mode guide" in out
    assert "CLI:" not in out
