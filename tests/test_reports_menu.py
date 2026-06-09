"""Tests for interactive reports menu."""

from __future__ import annotations

import pytest

from mercury.reporting.interactive_menu import run_reports_menu


def test_run_reports_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_reports_menu(interactive=False)
    out = capsys.readouterr().out
    assert "REPORTS AND BACKUP HISTORY" in out
    assert "Backup root" in out
    assert "Latest tracked" in out
    assert "Verified sources" in out
    assert "Show backup history" in out
    assert "Show protection status" in out
    assert "Actions" not in out
    assert "╭" not in out


def test_run_reports_menu_no_duplicate_heading(capsys: pytest.CaptureFixture[str]) -> None:
    run_reports_menu(interactive=False)
    out = capsys.readouterr().out
    assert out.count("REPORTS AND BACKUP HISTORY") == 1
