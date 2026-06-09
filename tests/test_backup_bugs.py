"""Additional tests for backup bugs and edge cases."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mercury.backup.on_disk_index import build_on_disk_backup_list, latest_records_by_database
from mercury.backup.verification import verify_backup_artifacts
from mercury.backup.terminal.verify import print_on_disk_backup_list
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


def test_latest_records_by_database_keeps_newest_record_first(tmp_path: Path) -> None:
    older = tmp_path / "2026-05-29" / "erebus_threat_intel_prod"
    newer = tmp_path / "2026-05-30" / "erebus_threat_intel_prod"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    for path, backup_id, created_at in (
        (older, "old", "2026-05-29T10:00:00+00:00"),
        (newer, "new", "2026-05-30T10:00:00+00:00"),
    ):
        (path / "manifest.json").write_text(
            json.dumps(
                {
                    "backup_id": backup_id,
                    "database": "erebus_threat_intel_prod",
                    "backup_kind": "full",
                    "created_at": created_at,
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
    latest = latest_records_by_database(listing)
    assert len(latest) == 1
    assert latest[0].backup_id == "new"


def test_cli_backup_list_on_disk() -> None:
    backups = REPO_ROOT / "backups"
    if not any(backups.glob("*/*/manifest.json")):
        pytest.skip("no on-disk backups in repo")

    result = run_cli(
        "backup",
        "list",
        env={**os.environ, "MERCURY_BACKUP_ROOT": str(backups)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BACKUP LIST (on-disk)" in result.stdout


def test_print_on_disk_backup_list_recomputes_verified_status(tmp_path: Path, capsys) -> None:
    backup_dir = tmp_path / "2026-06-09" / "android_permission_intel"
    backup_dir.mkdir(parents=True)
    dump_name = "android_permission_intel_20260609_030126.sql.gz"
    schema_name = "android_permission_intel_20260609_030126.schema.sql.gz"
    (backup_dir / dump_name).write_bytes(b"dump-bytes\n")
    (backup_dir / schema_name).write_bytes(b"schema-bytes\n")
    manifest = {
        "backup_id": "android_permission_intel-full-20260609_030126_787",
        "database": "android_permission_intel",
        "backup_kind": "full",
        "created_at": "2026-06-09T03:01:26+00:00",
        "dump_file": dump_name,
        "schema_file": schema_name,
        "sha256": "",
        "schema_sha256": None,
        "size_bytes": 11,
        "schema_size_bytes": 12,
        "source_role": "shared_authority",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    from mercury.backup.checksum import write_checksum_file

    write_checksum_file(backup_dir, [dump_name, schema_name])
    listing = build_on_disk_backup_list(tmp_path)

    print_on_disk_backup_list(listing, compact=True, menu=True)
    out = capsys.readouterr().out
    assert "android_permission_intel" in out
    assert "verified" in out
