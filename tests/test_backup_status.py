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


def test_run_backup_batch_refuses_missing_live_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury.backup.batch_runner import run_backup_batch
    from mercury.core.safety import BACKUP_KIND_FULL

    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr(
        "mercury.backup.batch_runner.fetch_live_server_database_names",
        lambda: {
            "erebus_threat_intel_prod",
            "android_permission_intel",
            "scytaledroid_core_prod",
        },
    )
    monkeypatch.setattr(
        "mercury.backup.batch_runner.resolve_batch_sources",
        lambda live=False: [
            "erebus_threat_intel_prod",
            "android_permission_intel",
            "scytaledroid_core_prod",
            "obsidiandroid_core_prod",
        ],
    )

    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=False,
        live=True,
        policy=policy,
    )
    obsidian = next(result for result in batch.results if result.database == "obsidiandroid_core_prod")
    assert obsidian.refused is True
    assert "not present on the MariaDB server" in (obsidian.refusal_reason or "")
    assert batch.refused_count == 1
    assert batch.dry_run_count == 3


def test_build_backup_status_report_live_anchors_missing_configured_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.database.core import DatabaseInventory, record_from_name
    from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES
    from mercury.database.core.sources import SOURCE_LIVE

    inventory = DatabaseInventory(
        connection="connected",
        entries=[
            record_from_name("erebus_threat_intel_prod", SOURCE_LIVE, connected=True),
            record_from_name("android_permission_intel", SOURCE_LIVE, connected=True),
            record_from_name("scytaledroid_core_prod", SOURCE_LIVE, connected=True),
        ],
    )
    monkeypatch.setattr(
        "mercury.database.discovery.discover_for_planning",
        lambda live=False: inventory,
    )

    report = build_backup_status_report(
        live=True,
        policy=ExecutionPolicy(
            dry_run=False,
            live_actions_enabled=True,
            backup_root=tmp_path,
            config_path=tmp_path / "local.toml",
            allow_unsafe_backup_root=True,
        ),
    )
    assert report.source_count == len(ACTIVE_BACKUP_SOURCE_DATABASES)
    obsidian = next(entry for entry in report.entries if entry.database == "obsidiandroid_core_prod")
    assert obsidian.protection_status == "absent"
    assert report.absent_count >= 1
    assert report.missing_count == 0 or all(
        entry.protection_status != "missing" or entry.database != "obsidiandroid_core_prod"
        for entry in report.entries
    )


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


def test_build_backup_status_report_does_not_append_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury.state.ledger import DATABASE_BACKUPS_CSV

    state_root = tmp_path / "state"
    monkeypatch.setattr("mercury.state.ledger.resolve_state_root", lambda policy=None: state_root)
    _write_verified_backup(
        tmp_path,
        "android_permission_intel",
        "android_permission_intel-full-20260608_120000",
    )
    build_backup_status_report(
        live=False,
        selected=["android_permission_intel"],
        policy=ExecutionPolicy(
            dry_run=False,
            live_actions_enabled=True,
            backup_root=tmp_path,
            config_path=tmp_path / "local.toml",
            allow_unsafe_backup_root=True,
        ),
    )
    assert not (state_root / DATABASE_BACKUPS_CSV).exists()
    assert not (state_root / "operations.jsonl").exists()


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
