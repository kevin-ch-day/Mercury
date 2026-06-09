"""Tests for main menu dashboard."""

from pathlib import Path
from types import SimpleNamespace

from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.platform import PlatformInfo
from mercury.menu.dashboard import _sync_readiness_summary, dashboard_rows


def test_dashboard_rows_include_core_fields() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "MariaDB" in text
    assert "Execution mode" in text
    assert "Backup target" in text
    assert "Execution Safety" not in text


def test_dashboard_rows_include_extended_stats() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Source DBs" in text
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


def test_dashboard_rows_show_platform_when_not_fedora(monkeypatch) -> None:
    monkeypatch.setattr(
        "mercury.menu.dashboard.detect_platform",
        lambda: PlatformInfo(system="Windows", release="11"),
    )
    rows = dashboard_rows(probe_database=False)
    assert any("Platform" in row and "Windows seed-only" in row for row in rows)


def test_sync_readiness_summary_reports_none_verified() -> None:
    report = SimpleNamespace(
        entries=[
            SimpleNamespace(
                prod="erebus_threat_intel_prod",
                blockers=["No on-disk backup found for production source."],
            ),
            SimpleNamespace(
                prod="scytaledroid_core_prod",
                blockers=["No on-disk backup found for production source."],
            ),
        ],
        ready_count=0,
        blocked_count=2,
    )

    def fake_build_sync_readiness_report(*, live: bool):
        return report

    import mercury.sync.readiness as readiness

    original = readiness.build_sync_readiness_report
    readiness.build_sync_readiness_report = fake_build_sync_readiness_report
    try:
        ready, blocked, blocker = _sync_readiness_summary(
            live=True,
            verified_names=set(),
            source_names={
                "android_permission_intel",
                "erebus_threat_intel_prod",
                "scytaledroid_core_prod",
            },
        )
    finally:
        readiness.build_sync_readiness_report = original

    assert (ready, blocked) == (0, 2)
    assert blocker == "No verified full backups exist yet."


def test_sync_readiness_summary_reports_missing_sync_sources_when_shared_authority_verified() -> None:
    report = SimpleNamespace(
        entries=[
            SimpleNamespace(
                prod="erebus_threat_intel_prod",
                blockers=["No on-disk backup found for production source."],
            ),
            SimpleNamespace(
                prod="scytaledroid_core_prod",
                blockers=["No on-disk backup found for production source."],
            ),
        ],
        ready_count=0,
        blocked_count=2,
    )

    def fake_build_sync_readiness_report(*, live: bool):
        return report

    import mercury.sync.readiness as readiness

    original = readiness.build_sync_readiness_report
    readiness.build_sync_readiness_report = fake_build_sync_readiness_report
    try:
        _ready, _blocked, blocker = _sync_readiness_summary(
            live=True,
            verified_names={"android_permission_intel"},
            source_names={
                "android_permission_intel",
                "erebus_threat_intel_prod",
                "scytaledroid_core_prod",
            },
        )
    finally:
        readiness.build_sync_readiness_report = original

    assert blocker == "Verified backups missing for production sync sources."


def test_sync_readiness_summary_reports_missing_source_databases_for_partial_general_coverage() -> None:
    report = SimpleNamespace(
        entries=[
            SimpleNamespace(
                prod="erebus_threat_intel_prod",
                blockers=["No on-disk backup found for production source."],
            ),
            SimpleNamespace(
                prod="scytaledroid_core_prod",
                blockers=["No on-disk backup found for production source."],
            ),
        ],
        ready_count=0,
        blocked_count=2,
    )

    def fake_build_sync_readiness_report(*, live: bool):
        return report

    import mercury.sync.readiness as readiness

    original = readiness.build_sync_readiness_report
    readiness.build_sync_readiness_report = fake_build_sync_readiness_report
    try:
        _ready, _blocked, blocker = _sync_readiness_summary(
            live=True,
            verified_names={"erebus_threat_intel_prod"},
            source_names={
                "android_permission_intel",
                "erebus_threat_intel_prod",
                "scytaledroid_core_prod",
            },
        )
    finally:
        readiness.build_sync_readiness_report = original

    assert blocker == "Verified backups still missing for source databases."
