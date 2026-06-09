"""Tests for aggregate backup status and filtered batch selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.backup.batch_runner import BackupSourceSelectionError, select_batch_sources
from mercury.backup.status import build_backup_status_report
from mercury.core.execution_policy import ExecutionPolicy

from tests.conftest import run_cli


def _write_verified_backup(backup_root: Path, database: str, backup_id: str) -> None:
    backup_dir = backup_root / "2026-06-08" / database
    backup_dir.mkdir(parents=True, exist_ok=True)
    dump_name = f"{database}_20260608_120000.sql.gz"
    schema_name = f"{database}_20260608_120000.schema.sql.gz"
    (backup_dir / dump_name).write_bytes(b"dump-bytes\n")
    (backup_dir / schema_name).write_bytes(b"schema-bytes\n")
    manifest = {
        "backup_id": backup_id,
        "database": database,
        "backup_kind": "full",
        "created_at": "2026-06-08T12:00:00+00:00",
        "dump_file": dump_name,
        "schema_file": schema_name,
        "sha256": "unused",
        "size_bytes": 22,
        "source_role": "production" if database.endswith("_prod") else "shared_authority",
        "tool_used": "mariadb-dump",
        "verified": False,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    from mercury.backup.checksum import write_checksum_file

    write_checksum_file(backup_dir, [dump_name, schema_name])


def test_select_batch_sources_rejects_non_backup_source() -> None:
    with pytest.raises(BackupSourceSelectionError):
        select_batch_sources(selected=["erebus_threat_intel_dev"], live=False)


def test_build_backup_status_report_counts_verified_and_missing(tmp_path: Path) -> None:
    _write_verified_backup(
        tmp_path,
        "android_permission_intel",
        "android_permission_intel-full-20260608_120000",
    )
    report = build_backup_status_report(
        live=False,
        selected=["android_permission_intel", "erebus_threat_intel_prod"],
        policy=ExecutionPolicy(
            dry_run=False,
            live_actions_enabled=True,
            backup_root=tmp_path,
            config_path=tmp_path / "local.toml",
            allow_unsafe_backup_root=True,
        ),
    )
    assert report.verified_count == 1
    assert report.missing_count == 1
    assert report.failed_count == 0
    assert report.entries[0].protection_status == "verified"
    assert report.entries[1].protection_status == "missing"


def test_build_backup_status_report_marks_repo_local_root_untrusted(
    repo_root: Path,
) -> None:
    report = build_backup_status_report(
        live=False,
        selected=["android_permission_intel"],
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=repo_root / "backups",
            config_path=repo_root / "config" / "local.toml",
        ),
    )
    assert report.verified_count == 0
    assert report.warnings
    if report.entries[0].backup_directory is not None:
        assert report.entries[0].protection_status == "untrusted root"
    else:
        assert report.entries[0].protection_status == "missing"


def test_cli_backup_status_compact_report(tmp_path: Path) -> None:
    _write_verified_backup(
        tmp_path,
        "android_permission_intel",
        "android_permission_intel-full-20260608_120000",
    )
    env = {
        "MERCURY_BACKUP_ROOT": str(tmp_path),
        "MERCURY_ALLOW_UNSAFE_BACKUP_ROOT": "1",
    }
    result = run_cli("backup", "status", "--demo", "--db", "android_permission_intel", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Backup status" in result.stdout
    assert "android_permission_intel" in result.stdout
    assert "verified" in result.stdout


def test_cli_backup_all_alias_matches_batch_output() -> None:
    result = run_cli("backup", "all", "--demo", "--db", "android_permission_intel")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BACKUP BATCH" in result.stdout
