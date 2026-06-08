"""Tests for dry-run backup planning."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury.database.core import classify_database
from mercury.database.backup_planning import DEMO_DATABASES, build_backup_plan, build_demo_backup_plan
from mercury.backup.terminal.plan import print_backup_plan
from mercury.core.execution_policy import ExecutionPolicy


def test_backup_plan_excludes_dev_databases() -> None:
    plan = build_demo_backup_plan()
    dev_names = [n for n in DEMO_DATABASES if n.endswith("_dev")]
    for name in dev_names:
        assert name not in plan.backup_sources
        excluded_names = {e.name for e in plan.excluded}
        assert name in excluded_names


def test_backup_plan_includes_prod_databases() -> None:
    plan = build_demo_backup_plan()
    prod_names = [n for n in DEMO_DATABASES if n.endswith("_prod")]
    for name in prod_names:
        assert name in plan.backup_sources


def test_backup_plan_includes_android_permission_intel() -> None:
    plan = build_demo_backup_plan()
    assert "android_permission_intel" in plan.backup_sources


def test_backup_plan_excludes_restorecheck_and_unknown() -> None:
    plan = build_backup_plan(
        [
            "_restorecheck_erebus_threat_intel_prod_20260530",
            "random_test_db",
        ]
    )
    assert "_restorecheck_erebus_threat_intel_prod_20260530" not in plan.backup_sources
    assert "random_test_db" not in plan.backup_sources


def test_excluded_entries_have_reasons() -> None:
    plan = build_demo_backup_plan()
    for item in plan.excluded:
        assert item.reason
        assert item.role


def test_safety_notes_present() -> None:
    plan = build_demo_backup_plan()
    assert len(plan.safety_notes) > 0


def test_build_plan_from_custom_list() -> None:
    plan = build_backup_plan(["custom_prod", "custom_dev"])
    assert "custom_prod" in plan.backup_sources
    assert "custom_dev" not in plan.backup_sources


def test_all_demo_databases_classified() -> None:
    plan = build_demo_backup_plan()
    assert len(plan.classifications) == len(DEMO_DATABASES)
    for name in DEMO_DATABASES:
        assert classify_database(name).role.value


def test_backup_plan_displays_resolved_root_and_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup_root = Path("/home/secadmin/Laughlin/GitHub/Mercury/backups")
    monkeypatch.setattr(
        "mercury.backup.terminal.plan.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=backup_root,
        ),
    )
    print_backup_plan(build_demo_backup_plan())
    out = capsys.readouterr().out
    assert "backup root" in out
    assert "backup root state" in out
    assert "Repo-local fallback only" in out
    assert "Production sources" in out
    assert "Shared authority sources" in out
    assert "Excluded development targets" in out
    assert "Out of scope" not in out
    assert "android_permission_intel" in out
    assert "backup-only; sync not applicable by design" in out
    assert f"future: {backup_root}/" in out


def test_backup_plan_uses_one_timestamp_per_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str | None, str | None]] = []

    original = __import__("mercury.backup.terminal.plan", fromlist=["build_backup_layout"]).build_backup_layout

    def fake_layout(name: str, *, date=None, timestamp=None, now=None):
        seen.append((date, timestamp))
        return original(name, date=date, timestamp=timestamp, now=now)

    monkeypatch.setattr("mercury.backup.terminal.plan.build_backup_layout", fake_layout)
    monkeypatch.setattr(
        "mercury.backup.terminal.plan.datetime",
        type(
            "FixedDateTime",
            (),
            {"now": staticmethod(lambda tz=None: datetime(2026, 5, 30, 12, 0, 0, 123000, tzinfo=timezone.utc))},
        ),
    )
    print_backup_plan(build_demo_backup_plan())
    assert seen
    assert all(date == "2026-05-30" for date, _ in seen)
    assert all(timestamp == "20260530_120000_123" for _, timestamp in seen)
