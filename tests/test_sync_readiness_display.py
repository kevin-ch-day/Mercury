"""Tests for sync readiness terminal display."""

from __future__ import annotations

import pytest

from mercury.sync.readiness import (
    RestoreCredentialStatus,
    SyncReadinessEntry,
    SyncReadinessReport,
    assess_restore_credentials,
)
from mercury.sync.terminal.readiness import (
    print_sync_readiness_report,
    sync_menu_next_step,
    sync_menu_table_rows,
)
from mercury.sync.terminal.verification import print_sync_verification_report
from mercury.sync.verification import SyncVerificationEntry, SyncVerificationReport


def _sample_report(*, ready: bool = True) -> SyncReadinessReport:
    return SyncReadinessReport(
        mode="live",
        backup_root="/mnt/MERCURY_DATA_USB/mercury_backups",
        entries=[
            SyncReadinessEntry(
                prod="erebus_threat_intel_prod",
                expected_dev="erebus_threat_intel_dev",
                dev_listed=True,
                project="Erebus",
                latest_backup_dir="/mnt/backups/erebus",
                backup_verified=True,
                backup_id="erebus_threat_intel_prod-full-20260613_120000",
                backup_freshness="fresh",
                backup_age="12m ago",
                ready_for_sync_planning=ready,
                blockers=[] if ready else ["Backup artifacts are artifact-verified but freshness is stale; run full backup before prod→dev sync."],
            ),
            SyncReadinessEntry(
                prod="scytaledroid_core_prod",
                expected_dev="scytaledroid_core_dev",
                dev_listed=True,
                project="ScytaleDroid",
                latest_backup_dir="/mnt/backups/scytale",
                backup_verified=True,
                backup_id="scytaledroid_core_prod-full-20260613_120000",
                backup_freshness="fresh",
                backup_age="12m ago",
                ready_for_sync_planning=ready,
                blockers=[] if ready else ["Backup artifacts are artifact-verified but freshness is stale; run full backup before prod→dev sync."],
            ),
        ],
        ready_count=2 if ready else 0,
        blocked_count=0 if ready else 2,
        restore_credentials=RestoreCredentialStatus(
            configured=True,
            healthy=True,
            configured_user="mercury_dev_restore",
            observed_identity="mercury_dev_restore@localhost",
            detail="Configured · mercury_dev_restore@localhost",
        ),
    )


def test_menu_readiness_shows_table_with_backup_age(capsys: pytest.CaptureFixture[str]) -> None:
    print_sync_readiness_report(_sample_report(ready=True), compact=True, menu=True)
    out = capsys.readouterr().out
    assert "PROJECT" in out and "PROD → DEV" in out and "BACKUP" in out
    assert "erebus_threat_intel_prod → erebus_threat_intel_dev" in out
    assert "12m ago" in out
    assert "Choose Sync All Ready Databases [2]." in out
    assert "…" not in out
    assert "Restore credentials:" in out
    assert "Configured · mercury_dev_restore@localhost" in out


def test_menu_readiness_shows_actionable_blocker_text(capsys: pytest.CaptureFixture[str]) -> None:
    print_sync_readiness_report(_sample_report(ready=False), compact=True, menu=True)
    out = capsys.readouterr().out
    assert "backup stale" in out or "Ready" not in out.split("SYNC")[-1]
    assert "Run full backup" in out
    assert "0 eligible · 2 blocked" in out


def test_sync_menu_table_rows_show_exact_source_and_target_labels() -> None:
    rows = sync_menu_table_rows(_sample_report(ready=True))
    assert rows[0][1] == "erebus_threat_intel_prod → erebus_threat_intel_dev"
    assert rows[0][2] == "12m ago"
    assert rows[0][4] == "Ready"


def test_sync_menu_scope_lists_projects_in_refresh_order() -> None:
    from mercury.sync.terminal.readiness import sync_menu_context_fields

    report = _sample_report(ready=True)
    report.entries = [
        SyncReadinessEntry(
            prod="android_permission_intel",
            expected_dev="android_permission_intel_dev",
            dev_listed=True,
            project="Permission Intel",
            ready_for_sync_planning=True,
            sync_order=1,
        ),
        *report.entries,
    ]
    fields = sync_menu_context_fields(report, live_allowed=True)
    assert fields["Scope"].endswith("(Permission Intel, Erebus, ScytaleDroid)")


def test_sync_menu_next_step_when_all_ready() -> None:
    tag, message = sync_menu_next_step(_sample_report(ready=True), live_allowed=True)
    assert tag == "warn"
    assert "Sync All Ready Databases" in message
    assert "Sync One Ready Pair" not in message


def test_sync_verification_labels_tables_and_views_not_all_objects(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = SyncVerificationReport(
        mode="live",
        complete_count=1,
        entries=[
            SyncVerificationEntry(
                source="erebus_threat_intel_prod",
                target="erebus_threat_intel_dev",
                status="complete",
                ready=True,
                live_objects=205,
                backup_objects=205,
            )
        ],
    )

    print_sync_verification_report(report, compact=True)

    out = capsys.readouterr().out
    assert "TABLES+VIEWS dev/backup" in out
    assert "OBJECTS dev/backup" not in out


def test_restore_credentials_status_is_readonly_and_identity_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.database.mariadb.config import MariaDbConnectionConfig

    config = MariaDbConnectionConfig(host="localhost", user="mercury_dev_restore")
    monkeypatch.setattr(
        "mercury.database.mariadb.config.load_mariadb_restore_config", lambda: config
    )
    monkeypatch.setattr(
        "mercury.database.mariadb.client.run_client_query",
        lambda _config, sql: "mercury_dev_restore@localhost\tmercury_dev_restore@localhost\tNONE",
    )
    status = assess_restore_credentials(probe=True)
    assert status.healthy is True
    assert status.detail == "Configured · mercury_dev_restore@localhost"

    monkeypatch.setattr(
        "mercury.database.mariadb.client.run_client_query",
        lambda _config, sql: "other@localhost\tother@localhost\tNONE",
    )
    assert assess_restore_credentials(probe=True).detail == "unexpected restore identity"


def test_restore_credentials_auth_failure_does_not_change_backup_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.database.mariadb.config import MariaDbConnectionConfig

    monkeypatch.setattr(
        "mercury.database.mariadb.config.load_mariadb_restore_config",
        lambda: MariaDbConnectionConfig(host="localhost", user="mercury_dev_restore"),
    )
    monkeypatch.setattr(
        "mercury.database.mariadb.client.run_client_query",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("auth failed")),
    )
    status = assess_restore_credentials(probe=True)
    report = _sample_report(ready=True).model_copy(update={"restore_credentials": status})
    assert report.ready_count == 2
    assert report.entries[0].backup_verified is True
    assert report.restore_credentials.healthy is False
    assert report.restore_credentials.detail == "authentication failed"
