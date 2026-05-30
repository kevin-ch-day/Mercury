"""Tests for dry-run backup planning."""

from mercury.database.core import classify_database
from mercury.database.backup_planning import DEMO_DATABASES, build_backup_plan, build_demo_backup_plan


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
    plan = build_demo_backup_plan()
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
