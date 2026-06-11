"""Tests for prod->dev sync target verification."""

from mercury.restore.readiness import TargetCompletenessEntry
from mercury.sync.verification import build_sync_verification_report


def test_build_sync_verification_report_maps_pairs(monkeypatch) -> None:
    from mercury.sync.readiness import SyncReadinessEntry, SyncReadinessReport

    monkeypatch.setattr(
        "mercury.sync.verification.build_sync_readiness_report",
        lambda **kwargs: SyncReadinessReport(
            mode="live",
            backup_root="/mnt/MERCURY_DATA_USB/mercury_backups",
            ready_count=1,
            blocked_count=1,
            entries=[
                SyncReadinessEntry(
                    prod="erebus_threat_intel_prod",
                    expected_dev="erebus_threat_intel_dev",
                    dev_listed=True,
                    project="Erebus",
                    ready_for_sync_planning=True,
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "mercury.sync.verification.build_target_completeness_entry_against_backup",
        lambda **kwargs: TargetCompletenessEntry(
            database="erebus_threat_intel_prod",
            target_database="erebus_threat_intel_dev",
            completeness_status="complete",
            backup_id="b1",
            live_object_count=10,
            backup_object_count=10,
        ),
    )

    report = build_sync_verification_report(live=True)
    assert report.complete_count == 1
    assert report.incomplete_count == 0
    assert report.entries[0].source == "erebus_threat_intel_prod"
    assert report.entries[0].target == "erebus_threat_intel_dev"
    assert report.entries[0].ready is True
