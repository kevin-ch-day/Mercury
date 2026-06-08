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
    assert "Target:" in out
    assert "Mode: DRY RUN" in out
    assert "Action: full backup" in out
    assert "DATABASE" in out
    assert "ROLE" in out
    assert "PLAN" in out
    assert "SYNC" in out
    assert "DATABASE                      ROLE      PLAN      SYNC" in out
    assert "android_permission_intel      shared    backup    n/a" in out
    assert "erebus_threat_intel_dev       dev       skip      refresh target" in out
    assert "android_permission_intel" in out
    assert "shared" in out
    assert "prod" in out
    assert "dev" in out
    assert "dev target" in out
    assert "skip" in out
    assert "excluded" not in out
    assert "dev target exists" not in out
    assert "Ignored databases:" not in out
    assert "\n[1] Refresh" in out
    assert "\n[2] Run full backup" in out
    assert "Verify on-disk backups" not in out
    assert "Backup plan (dry-run)" not in out
