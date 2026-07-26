"""Consolidated Restore and Disaster Recovery dashboard tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.restore.recovery_scope import (
    REQUIRED_RECOVERY_DATABASES,
    REQUIRED_RECOVERY_DEVELOPMENT,
    REQUIRED_RECOVERY_PRODUCTION,
)


def _entry(
    database: str,
    *,
    freshness: str = "fresh",
    verified: bool = True,
    restore_checked: bool = False,
):
    from mercury.backup.status import BackupStatusEntry

    backup_id = f"{database}-full-1"
    return BackupStatusEntry(
        database=database,
        role="prod" if database.endswith("_prod") or database == "android_permission_intel" else "dev",
        protection_status="verified" if verified else "missing",
        freshness=freshness,
        backup_age="1h ago",
        backup_id=backup_id if verified else None,
        backup_created_at="2026-07-26T18:32:00+00:00" if verified else None,
        restore_check_status="passed" if restore_checked else None,
        restore_check_backup_id=backup_id if restore_checked else None,
        manifest_verification_stamp=True,
        artifact_integrity_verified=verified,
    )


def test_required_recovery_scope_is_seven() -> None:
    assert len(REQUIRED_RECOVERY_DATABASES) == 7
    assert len(REQUIRED_RECOVERY_PRODUCTION) == 4
    assert len(REQUIRED_RECOVERY_DEVELOPMENT) == 3
    assert "android_permission_intel_dev" in REQUIRED_RECOVERY_DEVELOPMENT


def test_dashboard_lists_all_seven_and_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.backup.status import BackupStatusReport
    from mercury.restore.dashboard import build_recovery_dashboard
    from mercury.restore.check_plan import RestoreCheckPlan

    entries = [
        _entry("android_permission_intel", restore_checked=True),
        _entry("erebus_threat_intel_prod", restore_checked=True),
        _entry("obsidiandroid_core_prod", restore_checked=False),
        _entry("scytaledroid_core_prod", restore_checked=False),
        _entry("android_permission_intel_dev", restore_checked=True),
        _entry("erebus_threat_intel_dev", restore_checked=True),
        _entry("scytaledroid_core_dev", restore_checked=True),
    ]
    monkeypatch.setattr(
        "mercury.restore.dashboard.build_recovery_scope_status_report",
        lambda live=False: BackupStatusReport(
            backup_root="/mnt/x",
            backup_root_state="operator-mounted",
            source_count=7,
            verified_count=7,
            entries=entries,  # type: ignore[arg-type]
        ),
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.sealed_phase3b_package_note",
        lambda: "Phase 3B package sealed — routine backups do not replace it.",
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.resolve_operator_mount",
        lambda: Path("/mnt/MERCURY_DATA_V2"),
    )
    monkeypatch.setattr(
        "mercury.restore.check_cleanup.discover_restorecheck_names",
        lambda: [],
    )

    def fake_plan(name: str) -> RestoreCheckPlan:
        return RestoreCheckPlan(
            source_prod=name,
            restore_target=f"_restorecheck_{name}_20260726",
            allowed=True,
            backup_verified=True,
            backup_id=f"{name}-full-1",
            backup_directory="/tmp/b",
            dump_file="x.sql.gz",
        )

    monkeypatch.setattr(
        "mercury.restore.dashboard.build_restore_check_plan",
        fake_plan,
    )

    dash = build_recovery_dashboard(live=False)
    assert [row.database for row in dash.rows] == list(REQUIRED_RECOVERY_DATABASES)
    assert dash.production_backed_up == 4
    assert dash.development_backed_up == 3
    assert dash.restore_checks_pending == 2
    assert "NOT READY" in dash.readiness
    assert "2 production restore-checks pending" in dash.readiness
    assert "complete with warnings" not in dash.readiness.lower()
    assert dash.pending_names == [
        "obsidiandroid_core_prod",
        "scytaledroid_core_prod",
    ]
    assert dash.runnable_pending == dash.pending_names
    assert dash.deferred_dev_names == []
    assert dash.temp_restore_schemas == []
    assert "dev RC deferred" not in dash.scope_summary


def test_pending_plans_only_runnable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.backup.status import BackupStatusReport
    from mercury.restore.dashboard import (
        build_recovery_dashboard,
        pending_restore_check_plans,
    )
    from mercury.restore.check_plan import RestoreCheckPlan

    entries = [
        _entry(name, restore_checked=(name not in {
            "obsidiandroid_core_prod",
            "scytaledroid_core_prod",
            "scytaledroid_core_dev",
        }))
        for name in REQUIRED_RECOVERY_DATABASES
    ]
    monkeypatch.setattr(
        "mercury.restore.dashboard.build_recovery_scope_status_report",
        lambda live=False: BackupStatusReport(
            backup_root="/mnt/x",
            backup_root_state="operator-mounted",
            source_count=7,
            verified_count=7,
            entries=entries,  # type: ignore[arg-type]
        ),
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.sealed_phase3b_package_note",
        lambda: None,
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.resolve_operator_mount",
        lambda: Path("/mnt/x"),
    )
    monkeypatch.setattr(
        "mercury.restore.check_cleanup.discover_restorecheck_names",
        lambda: [],
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.build_restore_check_plan",
        lambda name: RestoreCheckPlan(
            source_prod=name,
            restore_target=f"_restorecheck_{name}_x",
            allowed=True,
            backup_verified=True,
            backup_id=f"{name}-full-1",
            backup_directory="/tmp",
            dump_file="d.sql.gz",
        ),
    )
    dash = build_recovery_dashboard(live=False)
    plans = pending_restore_check_plans(dash)
    assert [plan.source_prod for plan in plans] == [
        "obsidiandroid_core_prod",
        "scytaledroid_core_prod",
    ]
    assert "scytaledroid_core_dev" in dash.deferred_dev_names
    assert "scytaledroid_core_dev" not in dash.pending_names
    assert "scytaledroid_core_dev" not in dash.runnable_pending
    assert dash.restore_checks_pending == 2
    assert "2 production restore-checks pending" in dash.readiness
    assert "dev RC deferred (1)" in dash.scope_summary
    dev_row = next(row for row in dash.rows if row.database == "scytaledroid_core_dev")
    assert dev_row.restore_check == "Deferred"


def test_dashboard_render_and_back_is_readonly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.restore.dashboard import RecoveryDashboard, RecoveryDashboardRow
    from mercury.restore.interactive_dashboard import run_recovery_dashboard
    from mercury.backup.status import BackupStatusReport

    writes: list[str] = []
    rows = [
        RecoveryDashboardRow(
            database=name,
            role="prod",
            freshness="Fresh",
            artifact="Verified",
            restore_check="PENDING" if "scytaledroid" in name or "obsidian" in name else "Passed",
            last_backup="7/26/2026",
            backup_id=f"{name}-1",
            pending="scytaledroid" in name or "obsidian" in name,
            runnable="scytaledroid_core_prod" == name or "obsidiandroid_core_prod" == name,
        )
        for name in REQUIRED_RECOVERY_DATABASES
    ]
    dash = RecoveryDashboard(
        report=BackupStatusReport(
            backup_root="/mnt/x",
            backup_root_state="ok",
            source_count=7,
        ),
        rows=rows,
        readiness="NOT READY · 2 production restore-checks pending",
        production_backed_up=4,
        production_total=4,
        development_backed_up=3,
        development_total=3,
        restore_checks_passed=5,
        restore_checks_pending=2,
        pending_names=["obsidiandroid_core_prod", "scytaledroid_core_prod"],
        runnable_pending=["obsidiandroid_core_prod", "scytaledroid_core_prod"],
        deferred_dev_names=[],
        temp_restore_schemas=[],
        latest_backup_label="7/26/2026 1:47 PM CDT",
        package_line="Phase 3B sealed",
        runbooks_path="/mnt/MERCURY_DATA_V2/mercury_runbooks",
        scope_summary="7/7 backed up · prod RC 2/4",
        plans_by_database={},
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.build_recovery_dashboard",
        lambda: dash,
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.execute_restore_into_database",
        lambda **_k: writes.append("execute"),
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_stripped",
        lambda *_a, **_k: "0",
    )
    run_recovery_dashboard(interactive=True)
    out = capsys.readouterr().out
    assert "Restore and Disaster Recovery" in out
    assert "NOT READY · 2 production restore-checks pending" in out
    assert "7/7 backed up" in out
    assert "Clean up restore-check databases (none)" not in out
    for name in REQUIRED_RECOVERY_DATABASES:
        assert name in out
    assert "Temporary restore schemas: none" in out
    assert "Run pending restore-checks      recommended" in out
    assert "Next: complete 2 pending restore-checks." in out
    assert "Pending: obsidiandroid_core_prod, scytaledroid_core_prod" in out
    assert writes == []


def test_dev_unknown_freshness_renders_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.backup.status import BackupStatusReport
    from mercury.restore.dashboard import build_recovery_dashboard
    from mercury.restore.check_plan import RestoreCheckPlan

    entries = [
        _entry(
            name,
            restore_checked=name.endswith("_prod") or name == "android_permission_intel",
            freshness="unknown" if name.endswith("_dev") else "fresh",
        )
        for name in REQUIRED_RECOVERY_DATABASES
    ]
    monkeypatch.setattr(
        "mercury.restore.dashboard.build_recovery_scope_status_report",
        lambda live=False: BackupStatusReport(
            backup_root="/mnt/x",
            backup_root_state="operator-mounted",
            source_count=7,
            verified_count=7,
            entries=entries,
        ),
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.sealed_phase3b_package_note",
        lambda: None,
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.resolve_operator_mount",
        lambda: Path("/mnt/x"),
    )
    monkeypatch.setattr(
        "mercury.restore.check_cleanup.discover_restorecheck_names",
        lambda: [],
    )
    monkeypatch.setattr(
        "mercury.restore.dashboard.build_restore_check_plan",
        lambda name: RestoreCheckPlan(
            source_prod=name,
            restore_target=f"_restorecheck_{name}_x",
            allowed=True,
            backup_verified=True,
            backup_id=f"{name}-full-1",
            backup_directory="/tmp",
            dump_file="d.sql.gz",
        ),
    )
    dash = build_recovery_dashboard(live=False)
    assert dash.restore_checks_pending == 0
    assert "READY" in dash.readiness
    for row in dash.rows:
        if row.role == "dev":
            assert row.freshness == "OK"
            assert row.restore_check == "Deferred"
    assert len(dash.deferred_dev_names) == 3


def test_main_five_opens_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.run_recovery_dashboard",
        lambda **_k: called.append("dash"),
    )
    from mercury.menu.task_menus import run_recovery_hub

    run_recovery_hub()
    assert called == ["dash"]


def test_cli_restore_check_group_unchanged() -> None:
    from mercury.cli import app

    groups = {group.name for group in app.registered_groups}
    assert "restore-check" in groups
