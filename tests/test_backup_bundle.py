"""Tests for database backup manifest/runbook bundle output."""

from __future__ import annotations

import json
from pathlib import Path

from mercury.backup.bundle import build_database_bundle_plan, write_database_bundle_plan

from tests.test_backup_status import _write_verified_backup
from tests.conftest import run_cli


def test_build_database_bundle_plan_uses_backup_status(tmp_path: Path, monkeypatch) -> None:
    _write_verified_backup(
        tmp_path,
        "android_permission_intel",
        "android_permission_intel-full-20260608_120000",
    )
    monkeypatch.setattr(
        "mercury.backup.status.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": tmp_path,
                "backup_root_state": lambda self=None: "usb-mounted",
                "backup_root_is_within_repo": lambda self=None: False,
                "allow_unsafe_backup_root": True,
            },
        )(),
    )
    plan = build_database_bundle_plan(
        live=False,
        selected=["android_permission_intel"],
    )
    assert plan.source_count == 1
    assert plan.entries[0].database == "android_permission_intel"
    assert plan.entries[0].role == "shared"


def test_write_database_bundle_plan_writes_manifest_and_runbook(
    tmp_path: Path,
    monkeypatch,
) -> None:
    usb_root = tmp_path / "usb"
    manifest_dir = usb_root / "mercury_manifests"
    runbook_dir = usb_root / "mercury_runbooks"
    backup_root = usb_root / "mercury_backups"
    manifest_dir.mkdir(parents=True)
    runbook_dir.mkdir(parents=True)
    backup_root.mkdir(parents=True)

    _write_verified_backup(
        backup_root,
        "android_permission_intel",
        "android_permission_intel-full-20260608_120000",
    )

    monkeypatch.setattr("mercury.backup.bundle.REQUIRED_BACKUP_MOUNT", usb_root)
    monkeypatch.setattr(Path, "is_mount", lambda self: self.resolve() == usb_root.resolve())
    monkeypatch.setattr(
        "mercury.backup.bundle.load_repo_bundle_settings",
        lambda: type(
            "Settings",
            (),
            {
                "manifest_dir": manifest_dir,
                "runbook_dir": runbook_dir,
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.status.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": backup_root,
                "backup_root_state": lambda self=None: "usb-mounted",
                "backup_root_is_within_repo": lambda self=None: False,
                "allow_unsafe_backup_root": True,
            },
        )(),
    )

    plan = build_database_bundle_plan(live=False, selected=["android_permission_intel"])
    written = write_database_bundle_plan(plan)

    assert written.planned_index_manifest_path.exists()
    assert written.planned_index_runbook_path.exists()
    assert written.entries[0].planned_manifest_path.exists()
    assert written.entries[0].planned_runbook_path.exists()

    payload = json.loads(written.planned_index_manifest_path.read_text(encoding="utf-8"))
    assert payload["verified_count"] == 1
    assert payload["databases"][0]["database"] == "android_permission_intel"

    runbook = written.planned_index_runbook_path.read_text(encoding="utf-8")
    assert "Mercury database transfer runbook" in runbook
    assert "Restore-check uses disposable _restorecheck_* databases only." in runbook


def test_cli_backup_bundle_demo_plan(tmp_path: Path) -> None:
    env = {
        "MERCURY_BACKUP_ROOT": str(tmp_path / "backups"),
        "MERCURY_ALLOW_UNSAFE_BACKUP_ROOT": "1",
    }
    (tmp_path / "backups").mkdir(parents=True, exist_ok=True)
    result = run_cli("backup", "bundle", "--demo", "--db", "android_permission_intel", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Database backup bundle" in result.stdout
    assert "android_permission_intel" in result.stdout
