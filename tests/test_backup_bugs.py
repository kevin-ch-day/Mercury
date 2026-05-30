"""Additional tests for backup bugs and edge cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.backup.on_disk_index import build_on_disk_backup_list
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.safety import BACKUP_KIND_FULL

from tests.conftest import REPO_ROOT, run_cli


def test_full_backup_verify_requires_dump_not_schema_only(tmp_path: Path) -> None:
    """Full backup must not verify when only schema companion exists."""
    backup_dir = tmp_path / "2026-05-30" / "erebus_threat_intel_prod"
    backup_dir.mkdir(parents=True)
    schema_name = "erebus_threat_intel_prod_20260530_120000.schema.sql.gz"
    dump_name = "erebus_threat_intel_prod_20260530_120000.sql.gz"
    (backup_dir / schema_name).write_bytes(b"schema-only\n")

    manifest = {
        "backup_id": "test-full",
        "database": "erebus_threat_intel_prod",
        "backup_kind": BACKUP_KIND_FULL,
        "created_at": "2026-05-30T12:00:00Z",
        "dump_file": dump_name,
        "schema_file": schema_name,
        "sha256": "abc",
        "size_bytes": 0,
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "test",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (backup_dir / "checksum.sha256").write_text(
        f"{'a' * 64}  {schema_name}\n", encoding="utf-8"
    )

    result = verify_backup_artifacts(backup_dir)
    assert result.verified is False
    assert result.dump_exists is False


def test_build_on_disk_backup_list_finds_manifest(tmp_path: Path) -> None:
    backup_dir = tmp_path / "2026-05-30" / "droid_threat_intel_db_prod"
    backup_dir.mkdir(parents=True)
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "backup_id": "droid-full-test",
                "database": "droid_threat_intel_db_prod",
                "backup_kind": "full",
                "created_at": "2026-05-30T12:00:00+00:00",
                "dump_file": "d.sql.gz",
                "schema_file": None,
                "sha256": "abc",
                "size_bytes": 1,
                "source_role": "production",
                "tool_used": "mariadb-dump",
                "verified": True,
                "live_actions_enabled": True,
                "dry_run": False,
                "notes": "",
            }
        ),
        encoding="utf-8",
    )

    listing = build_on_disk_backup_list(tmp_path)
    assert len(listing.records) == 1
    assert listing.records[0].database == "droid_threat_intel_db_prod"
    assert listing.records[0].verified is True


def test_cli_backup_list_on_disk() -> None:
    backups = REPO_ROOT / "backups"
    if not any(backups.glob("*/*/manifest.json")):
        pytest.skip("no on-disk backups in repo")

    result = run_cli("backup", "list")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BACKUP LIST (on-disk)" in result.stdout
