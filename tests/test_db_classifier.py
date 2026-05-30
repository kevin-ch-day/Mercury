"""Tests for database name classification."""

import pytest

from mercury.database.core import DatabaseRole, classify_database


@pytest.mark.parametrize(
    ("name", "role", "backup_source", "dev_target", "manual_review"),
    [
        ("erebus_threat_intel_prod", DatabaseRole.PRODUCTION, True, False, False),
        ("scytaledroid_core_prod", DatabaseRole.PRODUCTION, True, False, False),
        ("erebus_threat_intel_dev", DatabaseRole.DEVELOPMENT, False, True, False),
        ("gecko_research_database_dev", DatabaseRole.DEVELOPMENT, False, True, False),
        ("android_permission_intel", DatabaseRole.SHARED_AUTHORITY, True, False, False),
        (
            "_restorecheck_erebus_threat_intel_prod_20260530",
            DatabaseRole.RESTORE_CHECK_TEMP,
            False,
            False,
            False,
        ),
        ("random_test_db", DatabaseRole.UNKNOWN, False, False, True),
    ],
)
def test_classify_database(
    name: str,
    role: DatabaseRole,
    backup_source: bool,
    dev_target: bool,
    manual_review: bool,
) -> None:
    result = classify_database(name)
    assert result.role == role
    assert result.backup_source is backup_source
    assert result.dev_target is dev_target
    assert result.manual_review is manual_review


def test_prod_is_production_backup_source() -> None:
    c = classify_database("erebus_threat_intel_prod")
    assert c.role == DatabaseRole.PRODUCTION
    assert c.backup_source is True


def test_dev_is_not_backup_source() -> None:
    c = classify_database("erebus_threat_intel_dev")
    assert c.role == DatabaseRole.DEVELOPMENT
    assert c.backup_source is False


def test_restorecheck_prefix_takes_precedence_over_prod_suffix() -> None:
    name = "_restorecheck_erebus_threat_intel_prod_20260530"
    c = classify_database(name)
    assert c.role == DatabaseRole.RESTORE_CHECK_TEMP
    assert c.backup_source is False
