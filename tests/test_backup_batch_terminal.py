"""Tests for compact backup batch terminal output."""

from __future__ import annotations

from mercury.backup.backup_runner import BackupExecutionResult
from mercury.backup.batch_runner import BackupBatchResult
from mercury.backup.manifest import BackupManifest
from mercury.backup.terminal.batch import print_backup_batch_result


def test_print_backup_batch_result_menu_shows_result_table(capsys) -> None:
    batch = BackupBatchResult(
        backup_kind="full",
        execute=True,
        sources=["android_permission_intel", "erebus_threat_intel_prod"],
        results=[
            BackupExecutionResult(
                database="android_permission_intel",
                backup_kind="full",
                dry_run=False,
                executed=True,
                backup_directory="backups/2026-06-09/android_permission_intel",
                backup_directory_path="/mnt/MERCURY_DATA_USB/mercury_backups/2026-06-09/android_permission_intel",
                manifest=BackupManifest(
                    backup_id="android_permission_intel-full-20260609_030126_787",
                    database="android_permission_intel",
                    backup_kind="full",
                    created_at="2026-06-09T03:01:26+00:00",
                    dump_file="android_permission_intel.sql.gz",
                    schema_file="android_permission_intel.schema.sql.gz",
                    sha256="abc",
                    size_bytes=123,
                    schema_sha256="abc2",
                    schema_size_bytes=45,
                    source_role="shared_authority",
                    tool_used="mariadb-dump",
                    verified=False,
                    live_actions_enabled=True,
                    dry_run=False,
                ),
            ),
            BackupExecutionResult(
                database="erebus_threat_intel_prod",
                backup_kind="full",
                dry_run=False,
                executed=True,
                backup_directory="backups/2026-06-09/erebus_threat_intel_prod",
                backup_directory_path="/mnt/MERCURY_DATA_USB/mercury_backups/2026-06-09/erebus_threat_intel_prod",
                manifest=BackupManifest(
                    backup_id="erebus_threat_intel_prod-full-20260609_030129_729",
                    database="erebus_threat_intel_prod",
                    backup_kind="full",
                    created_at="2026-06-09T03:01:29+00:00",
                    dump_file="erebus_threat_intel_prod.sql.gz",
                    schema_file="erebus_threat_intel_prod.schema.sql.gz",
                    sha256="def",
                    size_bytes=456,
                    schema_sha256="def2",
                    schema_size_bytes=78,
                    source_role="production",
                    tool_used="mariadb-dump",
                    verified=False,
                    live_actions_enabled=True,
                    dry_run=False,
                ),
            ),
        ],
        executed_count=2,
        refused_count=0,
        dry_run_count=0,
    )

    print_backup_batch_result(batch, compact=True, menu=True)
    out = capsys.readouterr().out
    assert "Execution mode" in out
    assert "Executed" in out
    assert "DATABASE" in out
    assert "RESULT" in out
    assert "BACKUP ID" in out
    assert "android_permission_intel" in out
    assert "written" in out
    assert "Next: verify source backups [3]." in out
