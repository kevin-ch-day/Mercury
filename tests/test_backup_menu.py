"""Tests for backup interactive menu."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.backup.interactive_menu import run_backup_menu
from mercury.core.execution_policy import ExecutionPolicy
from mercury.verify.interactive_menu import run_verify_menu


def test_run_backup_menu_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.should_probe_database_status",
        lambda: False,
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.latest_records_by_database",
        lambda listing: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_backup_status_report",
        lambda live=False: type("Report", (), {"entries": []})(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path / "backups",
            config_path=None,
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._storage_usage_fields",
        lambda policy: {
            "USB Path": str(policy.backup_root),
            "Used": "0 B",
            "Total": "1 GiB",
            "Free": "1 GiB",
            "Usage": "0%",
        },
    )
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


def test_backup_menu_uses_human_last_backup_format(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.interactive_menu import _render_backup_screen
    from mercury.database.backup_planning import build_backup_plan

    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_prod_dev_pairs",
        lambda names: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.latest_records_by_database",
        lambda listing: [
            type(
                "Record",
                (),
                {
                    "database": "android_permission_intel",
                    "created_at": "2026-06-09T15:01:26+00:00",
                },
            )()
        ],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_on_disk_backup_list",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_backup_status_report",
        lambda live=False: type(
            "Report",
            (),
            {
                "entries": [
                    type(
                        "Entry",
                        (),
                        {
                            "database": "android_permission_intel",
                            "protection_status": "verified",
                        },
                    )()
                ]
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": "/tmp/backups",
                "live_execution_allowed": lambda self=None: True,
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._storage_usage_fields",
        lambda policy: {"USB Path": "/tmp/backups"},
    )

    plan = build_backup_plan(["android_permission_intel"])
    _render_backup_screen(plan, show_title=False)
    out = capsys.readouterr().out
    assert "6/9/2026 3:01 PM" in out

# merged from test_verify_menu.py
def test_run_verify_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_verify_menu(interactive=False)
    out = capsys.readouterr().out
    assert "verified" in out
    assert "Rescan" in out
    assert "Verify all" in out
    assert "SOURCE ROLE" in out
    assert "shared authority source" in out
    assert "Actions" not in out

