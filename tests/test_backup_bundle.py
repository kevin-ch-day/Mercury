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

    monkeypatch.setattr("mercury.core.usb_mount.resolve_usb_mount", lambda **kwargs: usb_root)
    monkeypatch.setattr("mercury.core.usb_mount.usb_mount_is_active", lambda path, **kwargs: True)
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
    assert payload["package_status"] in {"complete", "complete with warnings"}
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


def test_print_database_bundle_plan_includes_state_summary(
    tmp_path: Path,
    capsys,
) -> None:
    from mercury.backup.bundle import DatabaseBundleEntry, DatabaseBundlePlan
    from mercury.backup.terminal.bundle import print_database_bundle_plan
    from mercury.state.summary import StateSummary
    import mercury.backup.terminal.bundle as terminal_mod

    terminal_mod.build_state_summary = lambda: StateSummary(
        state_root=tmp_path / "state",
        source="repo-local fallback",
        operations=5,
        database_backup_rows=3,
        database_bundle_rows=0,
        repo_bundle_rows=0,
        transfer_package_rows=0,
        sync_event_rows=0,
    )
    plan = DatabaseBundlePlan(
        generated_at="2026-06-09T00:00:00+00:00",
        backup_root=tmp_path / "backups",
        manifest_dir=tmp_path / "usb" / "mercury_manifests",
        runbook_dir=tmp_path / "usb" / "mercury_runbooks",
        planned_index_manifest_path=tmp_path / "usb" / "mercury_manifests" / "database_transfer_manifest.json",
        planned_index_runbook_path=tmp_path / "usb" / "mercury_runbooks" / "database_transfer_runbook.md",
        source_count=1,
        verified_count=1,
        missing_count=0,
        failed_count=0,
        stale_count=0,
        unknown_freshness_count=0,
        entries=[
            DatabaseBundleEntry(
                database="android_permission_intel",
                role="shared",
                protection_status="verified",
                backup_id="android-full-1",
                backup_directory=str(tmp_path / "backups" / "android_permission_intel"),
                planned_manifest_path=tmp_path / "usb" / "mercury_manifests" / "android.db_manifest.json",
                planned_runbook_path=tmp_path / "usb" / "mercury_runbooks" / "android.restore.md",
            )
        ],
        warnings=[],
    )
    print_database_bundle_plan(plan, executed=False)
    out = capsys.readouterr().out
    assert "State root" in out
    assert "State ops" in out
    assert "Package" in out


def test_print_database_bundle_plan_written_shows_artifact_paths(
    tmp_path: Path,
    capsys,
) -> None:
    from mercury.backup.bundle import DatabaseBundleEntry, DatabaseBundlePlan
    from mercury.backup.terminal.bundle import print_database_bundle_plan

    manifest_dir = tmp_path / "usb" / "mercury_manifests"
    runbook_dir = tmp_path / "usb" / "mercury_runbooks"
    stamp_date = "2026-06-09"
    stamp = "20260609_120000"
    plan = DatabaseBundlePlan(
        generated_at="2026-06-09T00:00:00+00:00",
        backup_root=tmp_path / "backups",
        manifest_dir=manifest_dir,
        runbook_dir=runbook_dir,
        planned_index_manifest_path=manifest_dir / stamp_date / f"database_transfer_manifest_{stamp}.json",
        planned_index_runbook_path=runbook_dir / stamp_date / f"database_transfer_runbook_{stamp}.md",
        source_count=1,
        verified_count=1,
        missing_count=0,
        failed_count=0,
        stale_count=1,
        unknown_freshness_count=0,
        entries=[
            DatabaseBundleEntry(
                database="erebus_threat_intel_prod",
                role="prod",
                protection_status="verified",
                backup_id="erebus_threat_intel_prod-full-20260608_120000",
                backup_directory=str(tmp_path / "backups" / "erebus_threat_intel_prod"),
                backup_age="2d ago",
                freshness="stale",
                planned_manifest_path=manifest_dir / stamp_date / f"erebus_{stamp}.db_manifest.json",
                planned_runbook_path=runbook_dir / stamp_date / f"erebus_{stamp}.restore.md",
            )
        ],
        warnings=[],
    )
    print_database_bundle_plan(plan, executed=True)
    out = capsys.readouterr().out
    assert "Bundle written to operator storage." in out
    assert "MANIFEST" in out
    assert "RUNBOOK" in out
    assert f"{stamp_date}/erebus_{stamp}.db_manifest.json" in out
    assert "Run full backup before handoff for stale source(s)" in out


def test_database_bundle_package_status_matrix() -> None:
    from mercury.core.handoff_status import (
        combine_handoff_status,
        database_bundle_package_status,
        handoff_write_requires_force,
    )

    assert database_bundle_package_status(
        source_count=0,
        verified_count=0,
        missing_count=0,
        failed_count=0,
    ) == "empty"
    assert database_bundle_package_status(
        source_count=4,
        verified_count=0,
        missing_count=4,
        failed_count=0,
    ) == "partial"
    assert database_bundle_package_status(
        source_count=2,
        verified_count=2,
        missing_count=0,
        failed_count=0,
    ) == "complete"
    assert database_bundle_package_status(
        source_count=2,
        verified_count=2,
        missing_count=0,
        failed_count=0,
        stale_count=1,
    ) == "complete with warnings"
    assert database_bundle_package_status(
        source_count=4,
        verified_count=3,
        missing_count=0,
        failed_count=0,
        absent_count=1,
    ) == "complete with warnings"
    assert database_bundle_package_status(
        source_count=2,
        verified_count=1,
        missing_count=1,
        failed_count=0,
    ) == "partial"
    assert combine_handoff_status("complete", "partial") == "partial"
    assert combine_handoff_status("complete with warnings", "complete") == "complete with warnings"
    assert handoff_write_requires_force("complete") is False
    assert handoff_write_requires_force("complete with warnings") is True


def test_backup_bundle_execute_requires_force_for_partial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mercury.backup.status.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": tmp_path / "backups",
                "backup_root_state": lambda self=None: "usb-mounted",
                "backup_root_is_within_repo": lambda self=None: False,
                "allow_unsafe_backup_root": True,
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.status.select_batch_sources",
        lambda live=False, selected=None: ["android_permission_intel"],
    )
    monkeypatch.setattr(
        "mercury.backup.bundle.build_backup_status_report",
        lambda **kwargs: type(
            "Report",
            (),
            {
                "backup_root": str(tmp_path / "backups"),
                "backup_root_state": "usb-mounted",
                "source_count": 1,
                "verified_count": 0,
                "missing_count": 1,
                "failed_count": 0,
                "stale_count": 0,
                "unknown_freshness_count": 0,
                "entries": [],
                "warnings": [],
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.bundle.load_repo_bundle_settings",
        lambda: type(
            "Settings",
            (),
            {
                "manifest_dir": tmp_path / "manifests",
                "runbook_dir": tmp_path / "runbooks",
            },
        )(),
    )
    result = run_cli(
        "backup",
        "bundle",
        "--execute",
        env={
            "MERCURY_BACKUP_ROOT": str(tmp_path / "backups"),
            "MERCURY_ALLOW_UNSAFE_BACKUP_ROOT": "1",
        },
    )
    assert result.returncode == 1
    assert "partial" in result.stdout + result.stderr


def test_state_summary_cli() -> None:
    result = run_cli("state", "summary")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "database_bundles" in result.stdout


def test_handoff_freshness_warning() -> None:
    from mercury.backup.freshness import handoff_freshness_warning

    assert handoff_freshness_warning(stale_count=1) is not None
    assert "stale" in handoff_freshness_warning(stale_count=1, unknown_count=1)
    assert handoff_freshness_warning() is None


def test_write_database_bundle_plan_records_ledger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    usb_root = tmp_path / "usb"
    manifest_dir = usb_root / "mercury_manifests"
    runbook_dir = usb_root / "mercury_runbooks"
    backup_root = usb_root / "mercury_backups"
    state_root = tmp_path / "state"
    manifest_dir.mkdir(parents=True)
    runbook_dir.mkdir(parents=True)
    backup_root.mkdir(parents=True)

    _write_verified_backup(
        backup_root,
        "android_permission_intel",
        "android_permission_intel-full-20260608_120000",
    )

    monkeypatch.setattr("mercury.core.usb_mount.resolve_usb_mount", lambda **kwargs: usb_root)
    monkeypatch.setattr("mercury.core.usb_mount.usb_mount_is_active", lambda path, **kwargs: True)
    monkeypatch.setattr("mercury.state.ledger.resolve_state_root", lambda policy=None: state_root)
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
    write_database_bundle_plan(plan)

    operations = (state_root / "operations.jsonl").read_text(encoding="utf-8")
    assert "database_bundle_written" in operations
    csv_text = (state_root / "database_bundles.csv").read_text(encoding="utf-8")
    assert "database_transfer_manifest_" in csv_text
    assert "complete" in csv_text
