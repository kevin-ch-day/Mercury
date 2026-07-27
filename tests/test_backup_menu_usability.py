"""Backup menu usability: Main [1] → Backup Operations, restore-check next action."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.backup.freshness import assess_operator_backup_next
from mercury.menu.options import MAIN_BACKUP, MAIN_RECOVERY
from mercury.menu.recommendation import build_main_menu_recommendation


def _fresh_entry(database: str, *, restore_checked: bool) -> SimpleNamespace:
    return SimpleNamespace(
        database=database,
        protection_status="verified",
        freshness="fresh",
        backup_age="1h ago",
        backup_id=f"{database}-full-1",
        restore_check_status="passed" if restore_checked else None,
        restore_check_backup_id=f"{database}-full-1" if restore_checked else None,
        manifest_verification_stamp=True,
    )


def _stale_entry(database: str) -> SimpleNamespace:
    return SimpleNamespace(
        database=database,
        protection_status="verified",
        freshness="stale",
        backup_age="3d ago",
        backup_id=f"{database}-full-1",
        restore_check_status=None,
        restore_check_backup_id=None,
        manifest_verification_stamp=True,
    )


def _writer_host() -> SimpleNamespace:
    return SimpleNamespace(
        writes_allowed=True,
        storage_availability="mounted",
        source_detach_preparation=False,
        package_verification_status="Pending",
        package_id="",
        destination_rehearsal_active=False,
        destination_rehearsal_in_progress=False,
    )


def _writer_lifecycle() -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(value="ATTACHED_WRITER_ENABLED"),
        writes_allowed=True,
        package_verified=False,
        package_status="Pending",
        package_id="",
        destination_rehearsal=False,
        drive_present=True,
        mounted=True,
        identity_ok=True,
        host_role=SimpleNamespace(value="SOURCE_OPERATION"),
        mount="/mnt/x",
    )


def test_main_one_routes_directly_to_backup_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_backup_menu",
        lambda **_k: called.append("backup_ops"),
    )
    from mercury.menu.actions import menu_actions
    from mercury.menu.task_menus import run_backup_hub

    run_backup_hub()
    assert called == ["backup_ops"]
    assert menu_actions()["1"].action_id == MAIN_BACKUP


def test_no_duplicate_guided_backup_route() -> None:
    from mercury.backup.menu_options import ACTION_BACKUP_SYNC_SESSION, BACKUP_MENU_OPTIONS

    guided = [
        (key, label)
        for key, label, action, _help in BACKUP_MENU_OPTIONS
        if action == ACTION_BACKUP_SYNC_SESSION
    ]
    assert guided == [("1", "Guided backup session")]


def test_restore_check_column_label(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from mercury.backup.interactive_menu import run_backup_menu
    from mercury.core.execution_policy import ExecutionPolicy

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
        lambda live=False: SimpleNamespace(entries=[], warnings=[]),
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
            "Backup root": str(policy.backup_root),
            "Backup writer": "Enabled",
            "Status": "ok",
            "Capacity": "1 GiB used · 9 GiB free (10%)",
        },
    )
    run_backup_menu(interactive=False)
    out = capsys.readouterr().out
    assert "RC" in out
    assert " VERIFY" not in out
    assert out.count("\n[1] Guided backup session") == 1
    assert "Next: Guided backup session [1]" in out


def test_recommendation_restore_check_when_fresh_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.backup.status.build_backup_status_report",
        lambda live=False: SimpleNamespace(
            entries=[
                _fresh_entry("android_permission_intel", restore_checked=True),
                _fresh_entry("scytaledroid_core_prod", restore_checked=False),
                _fresh_entry("obsidiandroid_core_prod", restore_checked=False),
            ]
        ),
    )
    monkeypatch.setattr(
        "mercury.core.runtime.should_probe_database_status",
        lambda: False,
    )
    next_action = assess_operator_backup_next(live=False)
    assert next_action["recommend"] == "restore_check"
    assert next_action["pending_restore_check"] == [
        "scytaledroid_core_prod",
        "obsidiandroid_core_prod",
    ]

    recommendation = build_main_menu_recommendation(
        host=_writer_host(),
        lifecycle=_writer_lifecycle(),
    )
    assert recommendation.recommended_action == MAIN_RECOVERY
    assert recommendation.recommended_action != MAIN_BACKUP
    assert "Restore-check pending" in recommendation.explanation
    assert "scytaledroid_core_prod" in recommendation.explanation
    assert "obsidiandroid_core_prod" in recommendation.explanation
    assert "scytaledroid_core_prod" in recommendation.recommended_label


def test_unknown_freshness_verified_does_not_force_backup() -> None:
    from mercury.backup.freshness import (
        backup_entry_needs_backup_work,
        backup_entry_needs_restore_check,
    )

    entry = SimpleNamespace(
        database="scytaledroid_core_prod",
        protection_status="verified",
        freshness="unknown",
        backup_age=None,
        backup_id="scytaledroid_core_prod-full-1",
        restore_check_status=None,
        restore_check_backup_id=None,
        manifest_verification_stamp=True,
    )
    assert backup_entry_needs_backup_work(entry) is False
    assert backup_entry_needs_restore_check(entry) is True


def test_assess_restore_check_when_freshness_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.backup.status.build_backup_status_report",
        lambda live=False: SimpleNamespace(
            entries=[
                SimpleNamespace(
                    database="scytaledroid_core_prod",
                    protection_status="verified",
                    freshness="unknown",
                    backup_age=None,
                    backup_id="scytaledroid_core_prod-full-1",
                    restore_check_status=None,
                    restore_check_backup_id=None,
                    manifest_verification_stamp=True,
                ),
                SimpleNamespace(
                    database="obsidiandroid_core_prod",
                    protection_status="verified",
                    freshness="unknown",
                    backup_age=None,
                    backup_id="obsidiandroid_core_prod-full-1",
                    restore_check_status=None,
                    restore_check_backup_id=None,
                    manifest_verification_stamp=True,
                ),
            ]
        ),
    )
    next_action = assess_operator_backup_next(live=False)
    assert next_action["recommend"] == "restore_check"
    assert next_action["pending_restore_check"] == [
        "scytaledroid_core_prod",
        "obsidiandroid_core_prod",
    ]


def test_recommendation_backup_when_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.backup.status.build_backup_status_report",
        lambda live=False: SimpleNamespace(
            entries=[_stale_entry("erebus_threat_intel_prod")]
        ),
    )
    monkeypatch.setattr(
        "mercury.core.runtime.should_probe_database_status",
        lambda: False,
    )
    next_action = assess_operator_backup_next(live=False)
    assert next_action["recommend"] == "backup"

    recommendation = build_main_menu_recommendation(
        host=_writer_host(),
        lifecycle=_writer_lifecycle(),
    )
    assert recommendation.recommended_action == MAIN_BACKUP


def test_entering_backup_ops_is_observe_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    writes: list[str] = []
    from mercury.backup.interactive_menu import run_backup_menu
    from mercury.core.execution_policy import ExecutionPolicy

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
        lambda live=False: SimpleNamespace(entries=[], warnings=[]),
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
            "Backup root": str(policy.backup_root),
            "Backup writer": "Enabled",
            "Status": "ok",
        },
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_backup_batch",
        lambda *a, **k: writes.append("batch"),
    )
    monkeypatch.setattr(
        "mercury.backup.session_wizard.run_backup_sync_wizard",
        lambda **_k: writes.append("wizard"),
    )
    answers = iter(["0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_stripped",
        lambda *_a, **_k: next(answers),
    )
    run_backup_menu(interactive=True)
    assert writes == []


def test_back_navigation_from_backup_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    exited: list[str] = []

    def fake_ops(*, interactive: bool = True) -> None:
        exited.append("opened")
        if interactive:
            exited.append("returned")

    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_backup_menu",
        fake_ops,
    )
    from mercury.menu.task_menus import run_backup_hub

    run_backup_hub()
    assert exited == ["opened", "returned"]


def test_backup_screen_next_pending_restore_check(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from mercury.backup.interactive_menu import _render_backup_screen
    from mercury.database.backup_planning import build_backup_plan

    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_prod_dev_pairs",
        lambda names: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.latest_records_by_database",
        lambda listing: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_on_disk_backup_list",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_backup_status_report",
        lambda live=False: SimpleNamespace(
            entries=[
                _fresh_entry("scytaledroid_core_prod", restore_checked=False),
                _fresh_entry("obsidiandroid_core_prod", restore_checked=False),
            ],
            warnings=[
                "Phase 3B package sealed — routine backups do not replace it."
            ],
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: SimpleNamespace(
            backup_root=tmp_path / "backups",
            backup_execution_allowed=lambda: True,
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._storage_usage_fields",
        lambda policy: {
            "Backup root": str(tmp_path / "backups"),
            "Backup writer": "Enabled",
            "Status": "ok",
            "Capacity": "1 GiB used · 9 GiB free (10%)",
        },
    )
    plan = build_backup_plan(
        ["scytaledroid_core_prod", "obsidiandroid_core_prod"]
    )
    _render_backup_screen(plan, show_title=True)
    out = capsys.readouterr().out
    assert "RC" in out
    assert "Next: Restore and disaster recovery [5] (2)" in out
    assert "Pending: scytaledroid_core_prod, obsidiandroid_core_prod" in out
    # Focus precedes storage fields for DEFCON glance.
    assert out.index("Next: Restore and disaster recovery [5]") < out.index("Backup root")
    assert "Back [0]" in out and "Main Menu [5]" in out
    assert "Do not run another backup" in out
    assert "Phase 3B package sealed — routine backups do not replace it." in out
    assert "Latest routine backups do not replace" not in out
    assert "[WARN] Restore-check required" not in out
    assert "Guided backup session      recommended" not in out


def test_full_backup_warns_when_protection_already_complete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.interactive_menu import _run_full_backup
    from mercury.database.backup_planning import build_backup_plan

    plan = build_backup_plan(["android_permission_intel"])
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._ensure_writes_then_continue",
        lambda: SimpleNamespace(available=True),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._production_protection_complete",
        lambda _plan: True,
    )
    answers = iter([False])  # decline redundant backup
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_yes_no",
        lambda *_a, **_k: next(answers),
    )
    ran: list[str] = []
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.run_backup_batch",
        lambda *_a, **_k: ran.append("batch"),
    )
    result = _run_full_backup(plan)
    out = capsys.readouterr().out
    assert result is None
    assert ran == []
    assert "already fresh and restore-checked" in out
    assert "Full backup cancelled" in out


def test_dump_heartbeat_emits_after_interval(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.interactive_menu import _DumpHeartbeat

    monkeypatch.setattr(
        "mercury.backup.interactive_menu._DUMP_HEARTBEAT_SECONDS",
        0.05,
    )
    with _DumpHeartbeat("scytaledroid_core_prod", interval=0.05):
        import time

        time.sleep(0.12)
    out = capsys.readouterr().out
    assert "still dumping" in out
    assert "scytaledroid_core_prod" in out


def test_compact_restore_result_is_short(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.restore.restore_runner import RestoreExecutionResult
    from mercury.restore.terminal.runner import print_restore_execution_result

    print_restore_execution_result(
        RestoreExecutionResult(
            source_database="erebus_threat_intel_prod",
            target_database="_restorecheck_erebus_threat_intel_prod_x",
            dump_path="/tmp/x.sql.gz",
            dry_run=False,
            executed=True,
            message="Restored erebus_threat_intel_prod into _restorecheck and dropped.",
            verification_passed=True,
            cleanup_dropped=True,
        ),
        compact=True,
    )
    out = capsys.readouterr().out
    assert "ok · verified · cleaned" in out
    assert "Restored erebus" not in out


def test_backup_screen_recommends_guided_when_stale(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from mercury.backup.interactive_menu import _render_backup_screen
    from mercury.database.backup_planning import build_backup_plan

    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_prod_dev_pairs",
        lambda names: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.latest_records_by_database",
        lambda listing: [],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_on_disk_backup_list",
        lambda _root: object(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_backup_status_report",
        lambda live=False: SimpleNamespace(
            entries=[_stale_entry("erebus_threat_intel_prod")],
            warnings=[],
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: SimpleNamespace(
            backup_root=tmp_path / "backups",
            backup_execution_allowed=lambda: True,
        ),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._storage_usage_fields",
        lambda policy: {
            "Backup root": str(tmp_path / "backups"),
            "Backup writer": "Enabled",
            "Status": "ok",
        },
    )
    plan = build_backup_plan(["erebus_threat_intel_prod"])
    _render_backup_screen(plan, show_title=True)
    out = capsys.readouterr().out
    assert "Next: Guided backup session [1]" in out
    assert "Guided backup session      recommended" in out
