from __future__ import annotations

import json

from mercury.storage.audit import (
    StorageAuditReport,
    hash_compare_trees,
    mount_targets_from_findmnt_output,
    write_storage_audit_report,
)
from mercury.storage.migrate_verify import MigrationVerifyReport


def test_hash_compare_trees_reports_durable_and_ephemeral_differences(tmp_path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "payload").mkdir(parents=True)
    (destination / "payload").mkdir(parents=True)
    (source / "payload" / "same.txt").write_text("same")
    (destination / "payload" / "same.txt").write_text("same")
    (source / "payload" / "changed.txt").write_text("source")
    (destination / "payload" / "changed.txt").write_text("target")
    (source / "mercury_logs").mkdir()
    (destination / "mercury_logs").mkdir()
    (source / "mercury_logs" / "live.log").write_text("new")
    (destination / "mercury_logs" / "live.log").write_text("old")

    files_hashed, identical, differences = hash_compare_trees(source, destination)

    assert files_hashed == 3
    assert identical == 1
    assert {(item.relative_path, item.ephemeral) for item in differences} == {
        ("payload/changed.txt", False),
        ("mercury_logs/live.log", True),
    }


def test_mount_targets_from_findmnt_output_detects_duplicate_mounts() -> None:
    assert mount_targets_from_findmnt_output("/mnt/MERCURY_DATA_V2\n/run/media/secadmin/MERCURY_DATA_V2\n") == (
        "/mnt/MERCURY_DATA_V2",
        "/run/media/secadmin/MERCURY_DATA_V2",
    )


def test_hash_compare_marks_source_change_during_hash(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    source_file = source / "payload.txt"
    destination_file = destination / "payload.txt"
    source_file.write_text("same")
    destination_file.write_text("same")

    def fake_hash(path):
        if path == destination_file:
            source_file.write_text("changed while hashing")
        return "digest"

    monkeypatch.setattr("mercury.storage.audit._sha256", fake_hash)
    _, identical, differences = hash_compare_trees(source, destination)

    assert identical == 1
    assert [(item.relative_path, item.issue) for item in differences] == [
        ("payload.txt", "source_changed_during_hash")
    ]


def test_audit_exit_codes_distinguish_warning_and_blocker() -> None:
    verified = MigrationVerifyReport(source_mount="source", dest_mount="dest")
    assert StorageAuditReport(verification=verified).exit_code == 0
    assert StorageAuditReport(verification=verified, warnings=("duplicate mount",)).exit_code == 1
    blocked = MigrationVerifyReport(source_mount="source", dest_mount="dest", blockers=("missing",))
    assert StorageAuditReport(verification=blocked).exit_code == 2


def test_audit_report_records_post_cutover_context() -> None:
    report = StorageAuditReport(
        verification=MigrationVerifyReport(source_mount="source", dest_mount="dest"),
        post_cutover=True,
    )
    assert report.post_cutover is True


def test_write_storage_audit_report_is_parseable_json(tmp_path) -> None:
    report = StorageAuditReport(
        verification=MigrationVerifyReport(source_mount="source", dest_mount="destination"),
        warnings=("duplicate mount",),
    )

    path = write_storage_audit_report(report, tmp_path / "audit.json")

    payload = json.loads(path.read_text())
    assert payload["warnings"] == ["duplicate mount"]
    assert payload["verification"]["source_mount"] == "source"
