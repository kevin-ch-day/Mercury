"""Tests for backup verify, locate helpers, and verification regressions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury.backup.dump_planner import build_dump_argv_for_config
from mercury.backup.backup_runner import execute_backup
from mercury.backup.layout import default_timestamp
from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.verification import verify_backup_artifacts, verify_backup_directory
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.config import MariaDbConnectionConfig

from tests.conftest import FIXED_DATE, FIXED_NOW, FIXED_TS, run_cli


def test_build_dump_argv_supports_unix_socket() -> None:
    cfg = MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    argv = build_dump_argv_for_config("erebus_threat_intel_prod", BACKUP_KIND_FULL, cfg)
    assert "--socket=/var/lib/mysql/mysql.sock" in argv
    assert "--protocol=SOCKET" in argv
    assert "-h" not in argv


def test_default_timestamp_includes_milliseconds() -> None:
    instant = datetime(2026, 5, 30, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert default_timestamp(instant) == "20260530_120000_123"


def test_find_latest_backup_directory_prefers_manifest_created_at(tmp_path: Path) -> None:
    older = tmp_path / "2026-05-29" / "erebus_threat_intel_prod"
    newer = tmp_path / "2026-05-30" / "erebus_threat_intel_prod"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)

    older_manifest = {
        "backup_id": "old",
        "database": "erebus_threat_intel_prod",
        "backup_kind": "full",
        "created_at": "2026-05-30T10:00:00+00:00",
        "dump_file": "old.sql.gz",
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    newer_manifest = {
        **older_manifest,
        "backup_id": "new",
        "created_at": "2026-05-30T18:00:00+00:00",
        "dump_file": "new.sql.gz",
    }
    (older / "manifest.json").write_text(json.dumps(older_manifest), encoding="utf-8")
    (newer / "manifest.json").write_text(json.dumps(newer_manifest), encoding="utf-8")

    latest = find_latest_backup_directory(tmp_path, "erebus_threat_intel_prod")
    assert latest == newer


def test_verify_rejects_manifest_database_mismatch(tmp_path: Path) -> None:
    backup_dir = tmp_path / "2026-05-30" / "erebus_threat_intel_prod"
    backup_dir.mkdir(parents=True)
    dump_name = "erebus_threat_intel_prod_20260530_120000.sql.gz"
    (backup_dir / dump_name).write_bytes(b"backup-data\n")
    manifest = {
        "backup_id": "test",
        "database": "erebus_threat_intel_prod",
        "backup_kind": "full",
        "created_at": "2026-05-30T12:00:00+00:00",
        "dump_file": dump_name,
        "schema_file": None,
        "sha256": "abc",
        "size_bytes": 12,
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    from mercury.backup.checksum import write_checksum_file

    write_checksum_file(backup_dir, [dump_name])

    ok = verify_backup_artifacts(backup_dir, database="erebus_threat_intel_prod")
    assert ok.verified is True

    wrong = verify_backup_artifacts(backup_dir, database="scytaledroid_core_prod")
    assert wrong.verified is False
    assert any("does not match requested" in issue for issue in wrong.issues)


def test_verify_backup_directory_updates_manifest(tmp_path: Path) -> None:
    policy = ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=tmp_path)

    def fake_runner(argv, env, output_path, _config):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"backup-bytes\n")

    execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        execute=True,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
        mariadb_config=MariaDbConnectionConfig(
            host="127.0.0.1", port=3306, user="root", password=""
        ),
        dump_runner=fake_runner,
    )

    backup_dir = tmp_path / FIXED_DATE / "erebus_threat_intel_prod"
    result = verify_backup_directory(backup_dir, update_manifest=True)
    assert result.verified is True

    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["verified"] is True


def test_cli_backup_verify_missing_backup() -> None:
    result = run_cli(
        "backup",
        "verify",
        "--db",
        "erebus_threat_intel_prod",
        "--no-latest",
    )
    assert result.returncode != 0
    assert "provide --path" in (result.stdout + result.stderr).lower()


def test_cli_verify_all_summary_on_partial_backups() -> None:
    result = run_cli("backup", "verify-all")
    assert "Verify-all summary" in result.stdout
    assert "skipped" in result.stdout.lower()
    assert result.returncode != 0
