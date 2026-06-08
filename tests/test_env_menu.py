"""Tests for interactive environment menu."""

from __future__ import annotations

import pytest

from mercury.env.interactive_menu import _print_live_mode_guide, run_env_menu


def test_run_env_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_env_menu(interactive=False)
    out = capsys.readouterr().out
    assert "ENVIRONMENT CHECK" in out
    assert "Runtime" in out
    assert "Python" in out
    assert "Rescan" in out
    assert "Live mode guide" in out
    assert "CLI:" not in out
    assert "╭" not in out
    assert "Submenu choice" not in out


def test_run_env_menu_no_duplicate_heading(capsys: pytest.CaptureFixture[str]) -> None:
    run_env_menu(interactive=False)
    out = capsys.readouterr().out
    assert out.count("ENVIRONMENT CHECK") == 1


def test_live_mode_guide_has_no_decorative_bullets(capsys: pytest.CaptureFixture[str]) -> None:
    _print_live_mode_guide()
    out = capsys.readouterr().out
    assert "LIVE MODE GUIDE" in out
    assert "◆" not in out
    assert "Before enabling live writes" in out
