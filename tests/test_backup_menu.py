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
    assert "Backup Operations" in out
    assert "USB target" not in out
    assert "USB Path:" in out
    assert "Used:" in out
    assert "Total:" in out
    assert "Free:" in out
    assert "Usage:" in out
    assert "Status:" not in out
    assert "Mode:" not in out
    assert "Action:" not in out
    assert "DATABASE" in out
    assert "STATUS" in out
    assert "LAST BACKUP" in out
    assert "TARGET" in out
    assert "android_permission_intel" in out
    assert "n/a" in out
    assert "erebus_threat_intel_prod" in out
    assert "erebus_threat_intel_dev" in out
    assert "refresh target" in out
    assert "dev target exists" not in out
    assert "PLAN" not in out
    assert "android_permission_intel" in out
    assert "skip" in out
    assert "excluded" not in out
    assert "Ignored databases:" not in out
    assert "\n[1] Refresh" in out
    assert "\n[2] Run full backup" in out
    assert "\n[3] Verify source backups" in out
    assert "\n[4] Restore-check source backups" in out
    assert "\n[5] Write DB bundle and runbooks" in out
    assert "Verify on-disk backups" not in out
    assert "Backup plan (dry-run)" not in out
