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
    assert dash.development_summary.startswith("3/3 backed up")
    assert "RC deferred" in dash.development_summary


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
    assert "dev RC deferred" not in dash.scope_summary
    assert dash.development_summary.startswith("3/3 backed up")
    assert "RC deferred" in dash.development_summary
    assert "scytaledroid_core_dev" in dash.deferred_dev_names
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
            role=(
                "dev"
                if name in REQUIRED_RECOVERY_DEVELOPMENT
                else "prod"
            ),
            freshness="Fresh",
            artifact="Verified",
            restore_check=(
                "PENDING"
                if name in {"scytaledroid_core_prod", "obsidiandroid_core_prod"}
                else ("Deferred" if name in REQUIRED_RECOVERY_DEVELOPMENT else "Passed")
            ),
            last_backup="7/26/2026",
            backup_id=f"{name}-1",
            pending=name in {"scytaledroid_core_prod", "obsidiandroid_core_prod"},
            runnable=name in {"scytaledroid_core_prod", "obsidiandroid_core_prod"},
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
        development_summary="3/3 backed up · latest 7/26/2026 1:34 PM CDT · RC deferred",
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
        "mercury.restore.interactive_dashboard._live_mode_ready",
        lambda: True,
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
    assert "Clean up restore-check databases" not in out
    assert "Temporary restore schemas" not in out
    assert "Production" in out
    assert "Development (RC lane deferred)" not in out
    assert "Runbooks" not in out
    assert "3/3 backed up" in out
    assert "RC deferred" in out
    assert "Selected production restore-checks" in out
    assert "LAST BACKUP" in out
    for name in REQUIRED_RECOVERY_PRODUCTION:
        assert name in out
    # Development DBs are summarized, not listed in a table.
    for name in REQUIRED_RECOVERY_DEVELOPMENT:
        assert name not in out
    assert "Run pending restore-checks (2)      recommended" in out
    assert "Next: Run pending restore-checks [1] (2)" in out
    assert "Pending: obsidiandroid_core_prod, scytaledroid_core_prod" in out
    assert "never *_prod" in out
    assert writes == []


def test_dashboard_hides_cleanup_and_rejects_choice_three(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.restore.dashboard import RecoveryDashboard, RecoveryDashboardRow
    from mercury.restore.interactive_dashboard import run_recovery_dashboard
    from mercury.backup.status import BackupStatusReport

    dash = RecoveryDashboard(
        report=BackupStatusReport(
            backup_root="/mnt/x",
            backup_root_state="ok",
            source_count=7,
        ),
        rows=[
            RecoveryDashboardRow(
                database="android_permission_intel",
                role="prod",
                freshness="Fresh",
                artifact="Verified",
                restore_check="Passed",
                last_backup="-",
                backup_id="x",
                pending=False,
                runnable=False,
            )
        ],
        readiness="READY · production restore-checks complete",
        production_backed_up=1,
        production_total=1,
        development_backed_up=0,
        development_total=0,
        restore_checks_passed=1,
        restore_checks_pending=0,
        pending_names=[],
        runnable_pending=[],
        deferred_dev_names=[],
        development_summary="none in scope",
        temp_restore_schemas=[],
        latest_backup_label="none",
        package_line="none",
        runbooks_path="/tmp",
        scope_summary="ok",
        plans_by_database={},
    )
    answers = iter(["3", "0"])
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.build_recovery_dashboard",
        lambda: dash,
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_stripped",
        lambda *_a, **_k: next(answers),
    )
    cleanup_calls: list[str] = []
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard._cleanup",
        lambda *_a, **_k: cleanup_calls.append("cleanup"),
    )
    run_recovery_dashboard(interactive=True)
    out = capsys.readouterr().out
    assert "Invalid choice" in out or "invalid" in out.lower()
    assert cleanup_calls == []


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


def test_dashboard_live_mode_hint_when_gated(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.restore.dashboard import RecoveryDashboard, RecoveryDashboardRow
    from mercury.restore.interactive_dashboard import run_recovery_dashboard
    from mercury.backup.status import BackupStatusReport

    dash = RecoveryDashboard(
        report=BackupStatusReport(
            backup_root="/mnt/x",
            backup_root_state="ok",
            source_count=1,
        ),
        rows=[
            RecoveryDashboardRow(
                database="obsidiandroid_core_prod",
                role="prod",
                freshness="Fresh",
                artifact="Verified",
                restore_check="PENDING",
                last_backup="7/26/2026",
                backup_id="x",
                pending=True,
                runnable=True,
            )
        ],
        readiness="NOT READY · 1 production restore-checks pending",
        production_backed_up=1,
        production_total=1,
        development_backed_up=0,
        development_total=0,
        restore_checks_passed=0,
        restore_checks_pending=1,
        pending_names=["obsidiandroid_core_prod"],
        runnable_pending=["obsidiandroid_core_prod"],
        deferred_dev_names=[],
        development_summary="none in scope",
        temp_restore_schemas=[],
        latest_backup_label="7/26/2026",
        package_line="none",
        runbooks_path="/tmp",
        scope_summary="prod RC 0/1",
        plans_by_database={},
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.build_recovery_dashboard",
        lambda: dash,
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard._live_mode_ready",
        lambda: False,
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_stripped",
        lambda *_a, **_k: "0",
    )
    run_recovery_dashboard(interactive=True)
    out = capsys.readouterr().out
    assert "Run pending restore-checks (1)      recommended" in out
    assert "Live mode required first" in out
    assert "dry_run=false" in out


def test_selected_restore_check_numbered_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.restore.dashboard import RecoveryDashboard, RecoveryDashboardRow
    from mercury.restore.interactive_dashboard import _run_selected
    from mercury.restore.check_plan import RestoreCheckPlan
    from mercury.backup.status import BackupStatusReport

    plan = RestoreCheckPlan(
        source_prod="scytaledroid_core_prod",
        restore_target="_restorecheck_scytaledroid_core_prod_x",
        allowed=True,
        backup_verified=True,
        backup_id="s-1",
        backup_directory="/tmp",
        dump_file="d.sql.gz",
    )
    dash = RecoveryDashboard(
        report=BackupStatusReport(
            backup_root="/mnt/x",
            backup_root_state="ok",
            source_count=2,
        ),
        rows=[
            RecoveryDashboardRow(
                database="android_permission_intel",
                role="prod",
                freshness="Fresh",
                artifact="Verified",
                restore_check="Passed",
                last_backup="-",
                backup_id="a",
                pending=False,
                runnable=False,
            ),
            RecoveryDashboardRow(
                database="scytaledroid_core_prod",
                role="prod",
                freshness="Fresh",
                artifact="Verified",
                restore_check="PENDING",
                last_backup="-",
                backup_id="s",
                pending=True,
                runnable=True,
            ),
        ],
        readiness="NOT READY",
        production_backed_up=2,
        production_total=2,
        development_backed_up=0,
        development_total=0,
        restore_checks_passed=1,
        restore_checks_pending=1,
        pending_names=["scytaledroid_core_prod"],
        runnable_pending=["scytaledroid_core_prod"],
        deferred_dev_names=[],
        development_summary="none in scope",
        temp_restore_schemas=[],
        latest_backup_label="none",
        package_line="none",
        runbooks_path="/tmp",
        scope_summary="ok",
        plans_by_database={"scytaledroid_core_prod": plan},
    )
    executed: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_stripped",
        lambda *_a, **_k: "2",
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard._execute_plans",
        lambda plans: executed.extend(p.source_prod for p in plans),
    )
    _run_selected(dash)
    assert executed == ["scytaledroid_core_prod"]


def test_execute_plans_prints_progress_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.restore.check_plan import RestoreCheckPlan
    from mercury.restore.interactive_dashboard import _execute_plans
    from mercury.restore.restore_runner import RestoreExecutionResult

    plans = [
        RestoreCheckPlan(
            source_prod="obsidiandroid_core_prod",
            restore_target="_restorecheck_obsidiandroid_core_prod_x",
            allowed=True,
            backup_verified=True,
            backup_id="o-1",
            backup_directory="/tmp",
            dump_file="o.sql.gz",
        ),
        RestoreCheckPlan(
            source_prod="scytaledroid_core_prod",
            restore_target="_restorecheck_scytaledroid_core_prod_x",
            allowed=True,
            backup_verified=True,
            backup_id="s-1",
            backup_directory="/tmp",
            dump_file="s.sql.gz",
        ),
    ]
    calls: list[str] = []

    def fake_execute(**kwargs):
        name = kwargs["source_database"]
        calls.append(name)
        if name.startswith("obsidian"):
            raise RuntimeError("simulated mid-batch failure")
        return RestoreExecutionResult(
            source_database=name,
            target_database=kwargs["target_database"],
            dump_path=str(kwargs["dump_path"]),
            dry_run=False,
            executed=True,
            message=f"Restored {name}.",
            verification_passed=True,
        )

    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.load_execution_policy",
        lambda: type(
            "P",
            (),
            {"live_execution_allowed": staticmethod(lambda: True)},
        )(),
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_yes_no",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "mercury.restore.interactive_dashboard.execute_restore_into_database",
        fake_execute,
    )
    _execute_plans(plans)
    out = capsys.readouterr().out
    assert calls == ["obsidiandroid_core_prod", "scytaledroid_core_prod"]
    assert "Restore-check dump sizes:" not in out
    assert "Sizes  " in out
    assert "[1/2] obsidiandroid_core_prod" in out
    assert "[2/2] scytaledroid_core_prod" in out
    assert "Batch complete: 1 passed, 1 failed" in out


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
