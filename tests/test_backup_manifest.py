"""Tests for backup manifest and planned paths."""

from mercury.backup_layout import build_backup_layout
from mercury.backup_manifest import (
    BackupManifest,
    planned_backup_dir,
    planned_backup_files,
)
from mercury.safety import BACKUP_KIND_FULL


def test_planned_backup_dir_format() -> None:
    assert planned_backup_dir("erebus_threat_intel_prod", "2026-05-30") == (
        "backups/2026-05-30/erebus_threat_intel_prod/"
    )


def test_build_backup_layout_matches_legacy_helpers() -> None:
    layout = build_backup_layout(
        "erebus_threat_intel_prod",
        date="2026-05-30",
        timestamp="20260530_120000",
    )
    assert layout.directory == planned_backup_dir("erebus_threat_intel_prod", "2026-05-30")
    assert layout.full_dump_file in planned_backup_files(
        "erebus_threat_intel_prod", "20260530_120000"
    )


def test_planned_backup_files_include_manifest() -> None:
    files = planned_backup_files("erebus_threat_intel_prod", "20260530_120000")
    assert "manifest.json" in files
    assert any(f.endswith(".sql.gz") for f in files)


def test_backup_manifest_model() -> None:
    from datetime import datetime, timezone

    m = BackupManifest(
        backup_id="test-1",
        database="erebus_threat_intel_prod",
        backup_kind=BACKUP_KIND_FULL,
        created_at=datetime.now(timezone.utc),
        dump_file="erebus_threat_intel_prod_20260530.sql.gz",
        source_role="production",
    )
    assert m.backup_kind == "full"
    assert m.verified is False
