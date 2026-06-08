"""Backup execution layer: dry-run defaults, live gates, manifests, checksums."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury.backup.backup_runner import (
    BackupExecutionError,
    assert_not_production_restore_target,
    assert_safe_backup_source,
    execute_backup,
    plan_backup_execution,
)
from mercury.backup.layout import build_backup_layout
from mercury.backup.manifest import BackupManifest, build_backup_manifest
from mercury.backup.checksum import sha256_file, verify_checksums, write_checksum_file
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy, resolve_backup_root
from mercury.core.paths import REPO_ROOT
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY, LIVE_ACTIONS_ENABLED
from mercury.backup.verification import verify_backup_artifacts

FIXED_DATE = "2026-05-30"
FIXED_TS = "20260530_120000"
FIXED_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def _dry_policy(tmp_path: Path) -> ExecutionPolicy:
    return ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "backups",
    )


def _live_policy(tmp_path: Path) -> ExecutionPolicy:
    return ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path / "backups",
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )


def _fake_dump_runner(
    argv: list[str],
    env: dict[str, str],
    output_path: Path,
    _config,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"FAKE_GZIP_BACKUP_CONTENT\n")


def test_default_policy_is_dry_run_seed() -> None:
    policy = load_execution_policy(
        local_config=Path("/nonexistent/local.toml"),
        dry_run_override=None,
        live_actions_override=None,
    )
    assert policy.dry_run is True
    assert policy.live_actions_enabled is LIVE_ACTIONS_ENABLED
    assert policy.live_execution_allowed() is False


def test_live_execution_requires_non_repo_backup_root() -> None:
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=REPO_ROOT / "backups",
        config_path=Path("/tmp/local.toml"),
    )
    reason = policy.refusal_reason()
    assert reason is not None
    assert "repo-local" in reason.lower()


def test_resolve_backup_root_accepts_absolute_usb_path(tmp_path: Path) -> None:
    usb_root = tmp_path / "run" / "media" / "secadmin" / "MERCURY_USB" / "mercury_backups"
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        "[mercury]\n"
        f'backup_root = "{usb_root}"\n',
        encoding="utf-8",
    )

    resolved = resolve_backup_root(local_config=config_path)
    assert resolved == usb_root.resolve()


def test_live_execution_refuses_unmounted_usb_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury import core
    from collections import namedtuple

    usb_mount = tmp_path / "mnt" / "MERCURY_DATA_USB"
    backup_root = usb_mount / "mercury_backups"
    backup_root.mkdir(parents=True)
    monkeypatch.setattr(core.execution_policy, "REQUIRED_BACKUP_MOUNT", usb_mount)
    monkeypatch.setattr(core.execution_policy, "_path_is_mount", lambda path: False)
    usage = namedtuple("usage", "total used free")(100, 10, 50 * 1024 * 1024 * 1024)
    monkeypatch.setattr(core.execution_policy, "_disk_usage", lambda path: usage)
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=backup_root,
        config_path=tmp_path / "local.toml",
    )
    reason = policy.refusal_reason()
    assert reason is not None
    assert "not active" in reason.lower()


def test_live_execution_accepts_mounted_usb_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury import core
    from collections import namedtuple

    usb_mount = tmp_path / "mnt" / "MERCURY_DATA_USB"
    backup_root = usb_mount / "mercury_backups"
    backup_root.mkdir(parents=True)
    monkeypatch.setattr(core.execution_policy, "REQUIRED_BACKUP_MOUNT", usb_mount)
    monkeypatch.setattr(core.execution_policy, "_path_is_mount", lambda path: True)
    usage = namedtuple("usage", "total used free")(100, 10, 50 * 1024 * 1024 * 1024)
    monkeypatch.setattr(core.execution_policy, "_disk_usage", lambda path: usage)
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=backup_root,
        config_path=tmp_path / "local.toml",
    )
    assert policy.refusal_reason() is None


def test_live_execution_refuses_low_free_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mercury import core
    from collections import namedtuple

    usb_mount = tmp_path / "mnt" / "MERCURY_DATA_USB"
    backup_root = usb_mount / "mercury_backups"
    backup_root.mkdir(parents=True)
    monkeypatch.setattr(core.execution_policy, "REQUIRED_BACKUP_MOUNT", usb_mount)
    monkeypatch.setattr(core.execution_policy, "_path_is_mount", lambda path: True)
    usage = namedtuple("usage", "total used free")(100, 10, 5 * 1024 * 1024 * 1024)
    monkeypatch.setattr(core.execution_policy, "_disk_usage", lambda path: usage)
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=backup_root,
        config_path=tmp_path / "local.toml",
    )
    reason = policy.refusal_reason()
    assert reason is not None
    assert "20 gb" in reason.lower()


def test_dry_run_does_not_write_backup_files(tmp_path: Path) -> None:
    policy = _dry_policy(tmp_path)
    result = execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        execute=False,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
    )
    assert result.executed is False
    assert result.dry_run is True
    assert not list(tmp_path.rglob("*"))


def test_live_execution_refused_without_enable(tmp_path: Path) -> None:
    policy = _dry_policy(tmp_path)
    result = execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        execute=True,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
        dump_runner=_fake_dump_runner,
    )
    assert result.refused is True
    assert result.executed is False
    assert result.refusal_reason is not None
    assert "dry-run" in result.refusal_reason.lower() or "live actions" in result.refusal_reason.lower()
    assert not list(tmp_path.rglob("*.sql.gz"))


def test_live_execution_refused_when_backup_root_is_inside_repo(tmp_path: Path) -> None:
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=REPO_ROOT / "backups",
        config_path=tmp_path / "local.toml",
    )
    result = execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        execute=True,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
        dump_runner=_fake_dump_runner,
    )
    assert result.refused is True
    assert result.executed is False
    assert result.refusal_reason is not None
    assert "repo-local" in result.refusal_reason.lower()


def test_failed_live_backup_cleans_up_temporary_files(tmp_path: Path) -> None:
    policy = _live_policy(tmp_path)

    def failing_runner(argv, env, output_path, _config):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"partial\n")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        execute_backup(
            "erebus_threat_intel_prod",
            BACKUP_KIND_FULL,
            execute=True,
            policy=policy,
            date=FIXED_DATE,
            timestamp=FIXED_TS,
            now=FIXED_NOW,
            mariadb_config=_fake_mariadb_config(),
            dump_runner=failing_runner,
        )
    assert not list((tmp_path / "backups").rglob("*.tmp"))


def test_dev_database_excluded_from_backup() -> None:
    with pytest.raises(BackupExecutionError, match="not a backup source"):
        assert_safe_backup_source("erebus_threat_intel_dev")
    with pytest.raises(BackupExecutionError):
        execute_backup(
            "scytaledroid_core_dev",
            BACKUP_KIND_FULL,
            execute=False,
        )


def test_unknown_database_requires_manual_review() -> None:
    with pytest.raises(BackupExecutionError, match="manual review"):
        assert_safe_backup_source("random_test_db")


def test_restorecheck_excluded_from_backup() -> None:
    with pytest.raises(BackupExecutionError, match="Restore-check temp"):
        assert_safe_backup_source("_restorecheck_erebus_threat_intel_prod_20260530")


def test_production_restore_target_blocked() -> None:
    with pytest.raises(BackupExecutionError, match="production"):
        assert_not_production_restore_target("erebus_threat_intel_prod")
    with pytest.raises(BackupExecutionError, match="shared authority"):
        assert_not_production_restore_target("android_permission_intel", operation="restore")


def test_manifest_fields_are_stable() -> None:
    manifest = build_backup_manifest(
        backup_id="erebus_threat_intel_prod-full-20260530_120000",
        database="erebus_threat_intel_prod",
        backup_kind=BACKUP_KIND_FULL,
        created_at=FIXED_NOW,
        source_role="production",
        dump_file="erebus_threat_intel_prod_20260530_120000.sql.gz",
        dump_sha256="abc123",
        dump_size_bytes=1024,
        schema_file="erebus_threat_intel_prod_20260530_120000.schema.sql.gz",
        schema_sha256="def456",
        schema_size_bytes=512,
        tool_used="mariadb-dump",
        live_actions_enabled=True,
        dry_run=False,
        notes="test",
    )
    data = json.loads(manifest.model_dump_json())
    assert set(data.keys()) == {
        "backup_id",
        "database",
        "backup_kind",
        "created_at",
        "dump_file",
        "schema_file",
        "sha256",
        "schema_sha256",
        "size_bytes",
        "schema_size_bytes",
        "source_role",
        "tool_used",
        "verified",
        "live_actions_enabled",
        "dry_run",
        "notes",
    }
    assert data["backup_kind"] == "full"
    assert data["live_actions_enabled"] is True


def test_live_execution_writes_manifest_and_checksum(tmp_path: Path) -> None:
    policy = _live_policy(tmp_path)
    result = execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_SCHEMA_ONLY,
        execute=True,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
        mariadb_config=_fake_mariadb_config(),
        dump_runner=_fake_dump_runner,
    )
    assert result.executed is True
    assert result.refused is False
    assert result.manifest is not None

    backup_dir = tmp_path / "backups" / FIXED_DATE / "erebus_threat_intel_prod"
    assert (backup_dir / "manifest.json").exists()
    assert (backup_dir / "checksum.sha256").exists()
    assert (backup_dir / "backup_report.md").exists()

    verification = verify_backup_artifacts(backup_dir)
    assert verification.verified is True
    assert verification.checksum_matches is True
    assert verification.role_ok is True


def test_full_backup_writes_dump_and_schema_companion(tmp_path: Path) -> None:
    policy = _live_policy(tmp_path)
    execute_backup(
        "android_permission_intel",
        BACKUP_KIND_FULL,
        execute=True,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
        mariadb_config=_fake_mariadb_config(),
        dump_runner=_fake_dump_runner,
    )
    backup_dir = tmp_path / "backups" / FIXED_DATE / "android_permission_intel"
    layout = build_backup_layout(
        "android_permission_intel",
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    assert (backup_dir / layout.full_dump_file).exists()
    assert (backup_dir / layout.schema_dump_file).exists()


def test_checksum_verification_catches_missing_and_mismatch(tmp_path: Path) -> None:
    backup_dir = tmp_path / "artifact"
    backup_dir.mkdir()
    artifact = backup_dir / "sample.sql.gz"
    artifact.write_bytes(b"content-a")

    write_checksum_file(backup_dir, ["sample.sql.gz"])
    checksum_path = backup_dir / "checksum.sha256"
    ok, issues = verify_checksums(backup_dir, checksum_path)
    assert ok is True
    assert issues == []

    artifact.write_bytes(b"content-b")
    ok, issues = verify_checksums(backup_dir, checksum_path)
    assert ok is False
    assert any("mismatch" in issue for issue in issues)

    artifact.unlink()
    ok, issues = verify_checksums(backup_dir, checksum_path)
    assert ok is False
    assert any("Missing" in issue for issue in issues)


def test_checksum_verification_catches_empty_file(tmp_path: Path) -> None:
    backup_dir = tmp_path / "empty"
    backup_dir.mkdir()
    artifact = backup_dir / "empty.sql.gz"
    artifact.write_bytes(b"")
    write_checksum_file(backup_dir, ["empty.sql.gz"])
    ok, issues = verify_checksums(backup_dir, backup_dir / "checksum.sha256")
    assert ok is False
    assert any("empty" in issue.lower() for issue in issues)


def test_verify_backup_artifacts_fails_without_manifest(tmp_path: Path) -> None:
    backup_dir = tmp_path / "incomplete"
    backup_dir.mkdir()
    result = verify_backup_artifacts(backup_dir, database="erebus_threat_intel_prod")
    assert result.verified is False
    assert not result.manifest_exists


def test_plan_backup_execution_marks_refusal_when_live_disabled(tmp_path: Path) -> None:
    plan = plan_backup_execution(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        policy=_dry_policy(tmp_path),
        date=FIXED_DATE,
        timestamp=FIXED_TS,
    )
    assert plan.refused is True
    assert plan.command
    assert "erebus_threat_intel_prod" in plan.command


def _fake_mariadb_config():
    from mercury.database.mariadb.config import MariaDbConnectionConfig

    return MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="mercury_readonly",
        password="test-password",
    )


def test_manifest_sha256_matches_artifact(tmp_path: Path) -> None:
    policy = _live_policy(tmp_path)
    result = execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_SCHEMA_ONLY,
        execute=True,
        policy=policy,
        date=FIXED_DATE,
        timestamp=FIXED_TS,
        now=FIXED_NOW,
        mariadb_config=_fake_mariadb_config(),
        dump_runner=_fake_dump_runner,
    )
    assert result.manifest is not None
    backup_dir = tmp_path / "backups" / FIXED_DATE / "erebus_threat_intel_prod"
    artifact = backup_dir / result.manifest.dump_file
    assert result.manifest.sha256 == sha256_file(artifact)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mercury.cli", *args],
        capture_output=True,
        text=True,
    )


def test_cli_backup_run_dry_run_default() -> None:
    result = _run_cli(
        "backup",
        "run",
        "--db",
        "erebus_threat_intel_prod",
        "--kind",
        "full",
    )
    assert result.returncode == 0
    assert "BACKUP EXECUTION" in result.stdout
    assert "dry_run" in result.stdout.lower() or "dry-run" in result.stdout.lower()


def test_cli_backup_run_refuses_dev() -> None:
    result = _run_cli(
        "backup",
        "run",
        "--db",
        "erebus_threat_intel_dev",
        "--kind",
        "full",
    )
    assert result.returncode != 0
    assert "not a backup source" in (result.stdout + result.stderr).lower()


def test_cli_backup_run_execute_refused_in_seed() -> None:
    result = _run_cli(
        "backup",
        "run",
        "--db",
        "erebus_threat_intel_prod",
        "--kind",
        "schema_only",
        "--execute",
    )
    assert result.returncode != 0
