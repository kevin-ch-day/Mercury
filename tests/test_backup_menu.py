"""Tests for backup interactive menu."""

from __future__ import annotations

import os
from pathlib import Path
import time

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
        lambda live=False: type(
            "Report",
            (),
            {"entries": [], "stale_count": 0, "unknown_freshness_count": 0},
        )(),
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
    assert "USB Path" in out
    assert "Used" in out
    assert "Total" in out
    assert "Free" in out
    assert "Usage" in out
    assert "Status:" not in out
    assert "Mode:" not in out
    assert "Backup mode:" not in out
    assert "Action:" not in out
    assert "DATABASE" in out
    assert "FRESHNESS" in out
    assert "VERIFY" in out
    assert "SIZE" in out
    assert "LAST BACKUP" in out
    assert "ARTIFACT" not in out
    assert "android_permission_intel" in out
    assert "erebus_threat_intel_prod" in out
    assert "erebus_threat_intel_dev" not in out
    assert "refresh target" not in out
    assert "dev target exists" not in out
    assert "PLAN" not in out
    assert "android_permission_intel" in out
    assert "excluded" not in out
    assert "Ignored databases:" not in out
    assert "Fresh full backup needed before workstation handoff" in out
    assert "\n[1] Back up and sync this workstation" in out
    assert "\n[2] Run full database backup" in out
    assert "\n[3] Back up production databases" in out
    assert "\n[4] Verify source backups" in out
    assert "\n[5] Restore-check source backups" in out
    assert "\n[6] Write DB bundle and runbooks" in out
    assert "\n[8] Open workstation handoff" in out
    assert "\n[9] Back up development databases" in out
    assert "Verify on-disk backups" not in out
    assert "\n[7] Preview backup plan" in out
    from mercury.backup.menu_options import ACTION_VERIFY, backup_menu_hint

    assert backup_menu_hint(ACTION_VERIFY) == "Verify source backups [4]"


def test_backup_menu_section_spacing_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Blank-line section gaps without asserting timestamps, sizes, or width."""
    from mercury.backup.interactive_menu import _render_backup_screen, read_backup_choice
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
                    "size_bytes": 10465313,
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
                            "freshness": "fresh",
                            "backup_age": "1h ago",
                            "backup_id": "android_permission_intel-full-20260609_150126",
                            "restore_check_status": None,
                        },
                    )()
                ],
                "stale_count": 0,
                "unknown_freshness_count": 0,
                "warnings": [
                    "Sealed Phase 3B rehearsal package present "
                    "(20260722T055400Z_phase3b).\n"
                    "Latest routine backups do not replace it until restore-check and "
                    "handoff packaging explicitly promote them."
                ],
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": tmp_path / "backups",
                "backup_execution_allowed": lambda self=None: True,
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._storage_usage_fields",
        lambda policy: {
            "Backup root": str(tmp_path / "backups"),
            "Environment": "operator storage mounted",
            "Used": "1 GiB",
            "Total": "10 GiB",
            "Free": "9 GiB",
            "Usage": "10%",
            "Status": "ok",
        },
    )

    plan = build_backup_plan(["android_permission_intel"])
    _render_backup_screen(plan, show_title=True)
    out = capsys.readouterr().out
    lines = out.splitlines()

    def index_of(predicate):
        for index, line in enumerate(lines):
            if predicate(line):
                return index
        raise AssertionError(f"line not found for {predicate!r}\n{out}")

    title_i = index_of(lambda line: line.strip() == "Backup Operations")
    status_i = index_of(lambda line: "Status" in line and "ok" in line)
    header_i = index_of(lambda line: line.startswith("DATABASE"))
    assert status_i > title_i
    assert lines[status_i + 1] == ""
    assert header_i == status_i + 2

    # Last table body row is the database name line (not the rule).
    db_i = index_of(lambda line: line.startswith("android_permission_intel"))
    assert lines[db_i + 1] == ""

    warn_i = index_of(lambda line: "[WARN]" in line and "Restore-check required" in line)
    assert warn_i == db_i + 2

    phase_i = index_of(lambda line: "Sealed Phase 3B rehearsal package present" in line)
    assert phase_i == warn_i + 1
    assert "Latest routine backups" not in lines[phase_i]
    follow_i = index_of(lambda line: line.startswith("Latest routine backups do not replace"))
    assert follow_i == phase_i + 1
    assert lines[follow_i + 1] == ""

    menu_i = index_of(lambda line: line.startswith("[1] Back up and sync this workstation"))
    assert menu_i == follow_i + 2
    back_i = index_of(lambda line: line.startswith("[0] Back"))
    assert back_i > menu_i
    # No blank lines between consecutive menu choices.
    for index in range(menu_i, back_i):
        assert lines[index] != ""

    # Aligned field values: Status value starts at same column as Backup root value.
    root_line = next(line for line in lines if "Backup root" in line)
    status_line = next(line for line in lines if "Status" in line and "ok" in line)
    root_value_at = root_line.index("Backup root") + len("Backup root")
    while root_value_at < len(root_line) and root_line[root_value_at] == " ":
        root_value_at += 1
    status_value_at = status_line.index("Status") + len("Status")
    while status_value_at < len(status_line) and status_line[status_value_at] == " ":
        status_value_at += 1
    assert root_value_at == status_value_at

    prompts: list[str] = []

    def _capture_prompt(prompt: str) -> str:
        prompts.append(prompt)
        return "0"

    monkeypatch.setattr("mercury.menu.prompts.ask_safe", _capture_prompt)
    assert read_backup_choice() == "0"
    assert prompts and prompts[0].startswith("\nChoice:")


def test_backup_menu_warning_summary_uses_visible_status_labels(
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
        lambda listing: [
            type(
                "Record",
                (),
                {
                    "database": "android_permission_intel",
                    "created_at": "2026-06-09T15:01:26+00:00",
                    "size_bytes": 10465313,
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
                            "freshness": "unknown",
                            "backup_age": None,
                        },
                    )(),
                    type(
                        "Entry",
                        (),
                        {
                            "database": "obsidiandroid_core_prod",
                            "protection_status": "missing",
                            "freshness": "unknown",
                            "backup_age": None,
                        },
                    )(),
                ],
                "stale_count": 0,
                "unknown_freshness_count": 2,
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: type(
            "Policy",
            (),
            {
                "backup_root": tmp_path / "backups",
                "backup_execution_allowed": lambda self=None: True,
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._storage_usage_fields",
        lambda policy: {"USB Path": str(tmp_path / "backups")},
    )

    plan = build_backup_plan(["android_permission_intel", "obsidiandroid_core_prod"])
    _render_backup_screen(plan, show_title=False)
    out = capsys.readouterr().out
    assert "Fresh full backup needed before workstation handoff: 1 unknown, 1 missing." in out


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
                    "size_bytes": 10465313,
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
                            "freshness": "unknown",
                            "backup_age": "1h ago",
                        },
                    )()
                ],
                "stale_count": 0,
                "unknown_freshness_count": 1,
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
                "backup_execution_allowed": lambda self=None: True,
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu._storage_usage_fields",
        lambda policy: {"USB Path": "/tmp/backups"},
    )

    original_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        plan = build_backup_plan(["android_permission_intel"])
        _render_backup_screen(plan, show_title=False)
        out = capsys.readouterr().out
        assert "6/9/2026 10:01 AM" in out
        assert "1h ago" not in out
        assert "MiB" in out
        assert "Backup mode:" not in out
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


# merged from test_verify_menu.py
def test_run_backup_menu_executes_when_environment_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mercury.backup.interactive_menu import _run_backup
    from mercury.database.backup_planning import build_backup_plan

    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "backups",
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    written: list[bool] = []

    def fake_batch(*args, **kwargs):
        written.append(kwargs.get("execute") is True)
        from mercury.backup.batch_runner import BackupBatchResult

        return BackupBatchResult(
            backup_kind="full",
            execute=True,
            sources=["android_permission_intel"],
            executed_count=1,
        )

    monkeypatch.setattr("mercury.backup.interactive_menu.load_execution_policy", lambda: policy)
    monkeypatch.setattr("mercury.backup.interactive_menu.run_backup_batch", fake_batch)
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.print_backup_batch_result",
        lambda *args, **kwargs: None,
    )
    _run_backup(build_backup_plan(["android_permission_intel"]))
    assert written == [True]


def test_full_backup_can_include_dev_recovery_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.backup.batch_runner import BackupBatchResult, BatchVerificationSummary
    from mercury.backup.interactive_menu import _run_full_backup
    from mercury.database.backup_planning import build_backup_plan

    monkeypatch.setattr(
        "mercury.backup.interactive_menu.menu_prompts.ask_yes_no",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=Path("/tmp/mercury-test-backups"),
            config_path=None,
            allow_unsafe_backup_root=True,
        ),
    )

    calls: list[str] = []

    def fake_batch(*args, **kwargs):
        sources = kwargs.get("sources") or []
        allow_dev = kwargs.get("allow_development_backup")
        calls.append("dev" if allow_dev else "production")
        return BackupBatchResult(
            backup_kind="full",
            execute=True,
            sources=list(sources) or ["android_permission_intel"],
            executed_count=1,
            results=[],
        )

    monkeypatch.setattr("mercury.backup.interactive_menu.run_backup_batch", fake_batch)
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.print_backup_batch_result",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.verify_written_backup_batch",
        lambda batch, allow_development_backup=False: BatchVerificationSummary(verified=1),
    )
    monkeypatch.setattr(
        "mercury.backup.batch_runner.resolve_development_backup_sources",
        lambda live=False: ["erebus_threat_intel_dev"],
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.write_full_backup_run_receipt",
        lambda result: Path("/tmp/receipt.json"),
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.print_full_backup_run_result",
        lambda result: None,
    )

    result = _run_full_backup(build_backup_plan(["android_permission_intel"]))
    assert calls == ["production", "dev"]
    assert result is not None
    assert result.development.requested is True


def test_run_verify_menu_non_interactive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Non-interactive verify menu must not scan live operator storage or wait on input."""
    from mercury.backup.terminal.verify import VerifyMenuSummary
    import mercury.verify.interactive_menu as verify_menu

    monkeypatch.setattr(
        verify_menu,
        "run_verify_all_for_menu",
        lambda *, update_manifest=False: VerifyMenuSummary(
            verified=1,
            missing=0,
            failed=0,
            rows=[["android_permission_intel", "shared", "verified"]],
        ),
    )
    run_verify_menu(interactive=False)
    out = capsys.readouterr().out
    assert "verified" in out.lower()
    assert "Rescan" in out
    assert "Verify all" in out
    assert "ROLE" in out
    assert "SOURCE ROLE" not in out
    assert "shared" in out
    assert "shared authority source" not in out
    assert "Shared authority databases are backup-only" not in out
    assert "Actions" not in out


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (
            type(
                "Entry",
                (),
                {"protection_status": "verified", "freshness": "fresh"},
            )(),
            "Fresh",
        ),
        (
            type(
                "Entry",
                (),
                {"protection_status": "verified", "freshness": "stale"},
            )(),
            "Stale",
        ),
        (
            type(
                "Entry",
                (),
                {"protection_status": "verified", "freshness": "unknown"},
            )(),
            "Unknown",
        ),
        (
            type(
                "Entry",
                (),
                {"protection_status": "missing", "freshness": "unknown"},
            )(),
            "Missing",
        ),
        (
            type(
                "Entry",
                (),
                {"protection_status": "failed", "freshness": "unknown"},
            )(),
            "Unverified",
        ),
        (
            type(
                "Entry",
                (),
                {"protection_status": "untrusted root", "freshness": "unknown"},
            )(),
            "Warning",
        ),
    ],
)
def test_status_label_mapping(entry, expected: str) -> None:
    from mercury.backup.interactive_menu import _status_label

    assert _status_label(entry) == expected


def test_freshness_and_verify_columns_are_independent() -> None:
    from mercury.backup.interactive_menu import _freshness_label, _verify_label

    entry = type(
        "Entry",
        (),
        {
            "protection_status": "verified",
            "freshness": "stale",
            "backup_id": "erebus_threat_intel_prod-full-1",
            "restore_check_status": None,
            "restore_check_backup_id": None,
            "manifest_verification_stamp": True,
        },
    )()
    assert _freshness_label(entry) == "Stale"
    assert _verify_label(entry) == "Not restore-checked"
    unverified = type(
        "Entry",
        (),
        {"protection_status": "failed", "freshness": "fresh"},
    )()
    assert _freshness_label(unverified) == "Fresh"
    assert _verify_label(unverified) == "Verify failed"


def test_status_label_mapping_for_missing_entry() -> None:
    from mercury.backup.interactive_menu import _status_label

    assert _status_label(None) == "Missing"


def test_write_backup_bundle_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury.backup.bundle import DatabaseBundlePlan
    from mercury.backup.interactive_menu import _write_backup_bundle

    plan = DatabaseBundlePlan(
        generated_at="2026-06-09T00:00:00+00:00",
        backup_root=Path("/tmp/backups"),
        manifest_dir=Path("/tmp/manifests"),
        runbook_dir=Path("/tmp/runbooks"),
        planned_index_manifest_path=Path("/tmp/manifests/index.json"),
        planned_index_runbook_path=Path("/tmp/runbooks/index.md"),
        source_count=1,
        verified_count=1,
        missing_count=0,
        failed_count=0,
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.build_database_bundle_plan",
        lambda live: plan,
    )
    monkeypatch.setattr(
        "mercury.backup.interactive_menu.print_database_bundle_plan",
        lambda plan, executed=False: None,
    )
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_yes_no",
        lambda prompt, default=True: False,
    )

    def _fail_write(plan):  # noqa: ARG001
        raise AssertionError("write should not run when cancelled")

    monkeypatch.setattr("mercury.backup.interactive_menu.write_database_bundle_plan", _fail_write)
    _write_backup_bundle()
    assert "cancelled" in capsys.readouterr().out.lower()
