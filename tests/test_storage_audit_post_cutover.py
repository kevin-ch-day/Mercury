"""Post-cutover storage audits treat USB comparison as historical evidence."""

from __future__ import annotations

from mercury.storage.audit import StorageAuditReport
from mercury.storage.migrate_verify import MigrationVerifyReport, VerifyMismatch


def test_post_cutover_legacy_drift_is_a_warning_not_a_cutover_failure() -> None:
    verification = MigrationVerifyReport(
        source_mount="/mnt/MERCURY_DATA_USB",
        dest_mount="/mnt/MERCURY_DATA_V2",
        matched=10,
        mismatches=(VerifyMismatch("mercury_backups/new.sql.gz", "missing"),),
    )
    report = StorageAuditReport(verification=verification, post_cutover=True)

    assert report.ok is True
    assert report.exit_code == 1


def test_post_cutover_legacy_mount_blocker_is_a_warning() -> None:
    verification = MigrationVerifyReport(
        source_mount="/mnt/MERCURY_DATA_USB",
        dest_mount="/mnt/MERCURY_DATA_V2",
        blockers=("Legacy source not ready",),
    )
    report = StorageAuditReport(verification=verification, post_cutover=True)

    assert report.ok is True
    assert report.exit_code == 1


def test_post_cutover_primary_mount_blocker_remains_a_failure() -> None:
    verification = MigrationVerifyReport(
        source_mount="/mnt/MERCURY_DATA_USB",
        dest_mount="/mnt/MERCURY_DATA_V2",
        blockers=("Primary destination not ready: mount missing",),
    )
    report = StorageAuditReport(verification=verification, post_cutover=True)

    assert report.ok is False
    assert report.exit_code == 2
