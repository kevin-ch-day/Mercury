"""Tests for main menu dashboard."""

from mercury.menu.dashboard import dashboard_rows


def test_dashboard_rows_include_core_fields() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Database connection" in text
    assert "Backups location" in text
    assert "Mode" in text
    assert "dry-run" in text


def test_dashboard_rows_include_extended_stats() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Backup coverage" in text
    assert "On disk" in text
    assert "Prod→dev sync" in text
