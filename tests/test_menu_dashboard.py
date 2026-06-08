"""Tests for main menu dashboard."""

from pathlib import Path

from mercury.core.execution_policy import ExecutionPolicy
from mercury.menu.dashboard import dashboard_rows


def test_dashboard_rows_include_core_fields() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "MariaDB" in text
    assert "Target" in text
    assert "Mode" in text
    assert "DRY RUN" in text


def test_dashboard_rows_include_extended_stats() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Source DBs verified" in text
    assert "USB backups" in text
    assert "Sync pairs" in text
    assert "Blocker" in text


def test_dashboard_rows_warn_on_repo_local_backup_root(monkeypatch) -> None:
    monkeypatch.setattr(
        "mercury.menu.dashboard.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=Path("/home/secadmin/Laughlin/GitHub/Mercury/backups"),
        ),
    )
    monkeypatch.setattr(
        "mercury.core.runtime.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=Path("/home/secadmin/Laughlin/GitHub/Mercury/backups"),
        ),
    )
    rows = dashboard_rows(probe_database=False)
    assert any("repo-local fallback" in row for row in rows)
