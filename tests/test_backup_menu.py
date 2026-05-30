"""Tests for backup interactive menu."""

from __future__ import annotations

import pytest

from mercury.backup.interactive_menu import run_backup_menu


def test_run_backup_menu_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_backup_menu(interactive=False)
    out = capsys.readouterr().out
    assert "Rescan plan" in out
    assert "Run full backup" in out
    assert "Verify on-disk backups" in out
    assert "Backup plan (dry-run)" not in out
