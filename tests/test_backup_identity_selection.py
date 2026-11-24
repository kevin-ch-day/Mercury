"""Exact backup-ID selection, Phase 3B pinning, and receipt write guards."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mercury.backup.batch_runner import (
    BackupBatchResult,
    BatchVerificationSummary,
    FullBackupOutcome,
    LaneResult,
    apply_full_backup_run_evidence,
    build_full_backup_run_result,
    write_full_backup_run_receipt,
)
from mercury.backup.backup_runner import BackupExecutionResult
from mercury.backup.checksum import parse_checksum_file, write_checksum_file
from mercury.backup.content_contract import BackupContentContract, BackupObjectInventory
from mercury.backup.find_latest_backup import (
    find_backup_by_id,
    find_latest_artifact_verified_backup,
    find_latest_backup_directory,
    find_latest_restore_checked_backup,
    resolve_backup_directory,
)
from mercury.backup.manifest import BackupManifest
from mercury.backup.status import RestoreCheckLedgerRecord, build_backup_status_report
from mercury.backup.verification import manifest_verified_stamp, verify_backup_artifacts
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.deploy.selection import resolve_deployment_candidates
from mercury.restore.check_plan import build_restore_check_plan


def _write_backup(
    root: Path,
    *,
    database: str,
    backup_id: str,
    stamp: str,
    day: str,
    created_at: str,
    verified_manifest: bool = False,
) -> Path:
    backup_dir = root / day / database / stamp
    backup_dir.mkdir(parents=True)
    dump_name = f"{database}_{stamp}.sql.gz"
    schema_name = f"{database}_{stamp}.schema.sql.gz"
    dump_path = backup_dir / dump_name
    schema_path = backup_dir / schema_name
    dump_path.write_bytes(b"dump-bytes\n")
    with gzip.open(schema_path, "wt", encoding="utf-8") as handle:
        handle.write("CREATE TABLE `t1` (id INT);\n")
    write_checksum_file(backup_dir, [dump_name, schema_name])
    checksums = parse_checksum_file(backup_dir / "checksum.sha256")
    manifest = {
        "backup_id": backup_id,
        "database": database,
        "backup_kind": BACKUP_KIND_FULL,
        "created_at": created_at,
        "dump_file": dump_name,
        "schema_file": schema_name,
        "sha256": checksums.get(dump_name, ""),
        "schema_sha256": checksums.get(schema_name, ""),
        "size_bytes": dump_path.stat().st_size,
        "schema_size_bytes": schema_path.stat().st_size,
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": verified_manifest,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return backup_dir


def _executed(database: str, backup_id: str, directory: Path) -> BackupExecutionResult:
    return BackupExecutionResult(
        database=database,
        backup_kind="full",
        dry_run=False,
        executed=True,
        refused=False,
        backup_directory=str(directory),
        backup_directory_path=str(directory),
        manifest=BackupManifest(
            backup_id=backup_id,
            database=database,
            backup_kind="full",
            created_at="2026-07-22T14:10:40Z",
            dump_file=f"{database}.sql.gz",
            schema_file=f"{database}.schema.sql.gz",
            sha256="a" * 64,
            schema_sha256="b" * 64,
            size_bytes=100_000,
            schema_size_bytes=1000,
            source_role="production",
            tool_used="mariadb-dump",
            verified=False,
            live_actions_enabled=True,
            dry_run=False,
        ),
        content_contract=BackupContentContract(
            live=BackupObjectInventory(tables=["t1"]),
            dump=BackupObjectInventory(tables=["t1"]),
            verified=True,
        ),
    )


def test_phase3b_pinned_id_not_replaced_by_newer_routine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = "erebus_threat_intel_prod"
    phase3b_id = "erebus_threat_intel_prod-full-20260722_055400_000"
    routine_id = "erebus_threat_intel_prod-full-20260722_141000_000"
    older = _write_backup(
        tmp_path,
        database=database,
        backup_id=phase3b_id,
        stamp="20260722_055400_000",
        day="2026-07-22",
        created_at="2026-07-22T05:54:00+00:00",
    )
    newer = _write_backup(
        tmp_path,
        database=database,
        backup_id=routine_id,
        stamp="20260722_141000_000",
        day="2026-07-22",
        created_at="2026-07-22T14:10:00+00:00",
    )
    assert find_latest_backup_directory(tmp_path, database) == newer
    assert find_latest_artifact_verified_backup(tmp_path, database) == newer

    ledger = {
        phase3b_id: RestoreCheckLedgerRecord(
            database=database,
            backup_id=phase3b_id,
            status="passed",
            timestamp="2026-07-22T06:00:00+00:00",
            backup_path=str(older),
            target_schema="_restorecheck_erebus",
        )
    }
    monkeypatch.setattr(
        "mercury.backup.status.latest_restore_check_by_backup_id",
        lambda: ledger,
    )

    assert find_latest_restore_checked_backup(tmp_path, database) == older
    assert find_backup_by_id(tmp_path, phase3b_id, database=database) == older

    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path,
        allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr("mercury.restore.check_plan.load_execution_policy", lambda: policy)
    monkeypatch.setattr("mercury.restore.check_plan.should_probe_database_status", lambda: False)
    monkeypatch.setattr("mercury.deploy.selection.load_execution_policy", lambda: policy)
    monkeypatch.setattr(
        "mercury.deploy.selection.resolve_batch_sources",
        lambda live=False: [database],
    )

    pinned = build_restore_check_plan(database, backup_id=phase3b_id, require_backup_id=True)
    assert pinned.allowed
    assert pinned.backup_id == phase3b_id
    assert pinned.backup_directory == str(older)

    candidates = resolve_deployment_candidates(
        policy=policy,
        databases=[database],
        backup_ids={database: phase3b_id},
        require_backup_ids=True,
    )
    assert len(candidates) == 1
    assert candidates[0].backup_id == phase3b_id

    latest_verified = resolve_backup_directory(tmp_path, database, prefer="artifact_verified")
    assert latest_verified == newer
    assert latest_verified != older


def test_restore_check_requires_backup_id_noninteractive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = "android_permission_intel"
    _write_backup(
        tmp_path,
        database=database,
        backup_id="android_permission_intel-full-1",
        stamp="20260722_100000_000",
        day="2026-07-22",
        created_at="2026-07-22T10:00:00+00:00",
    )
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path,
        allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr("mercury.restore.check_plan.load_execution_policy", lambda: policy)
    monkeypatch.setattr("mercury.restore.check_plan.should_probe_database_status", lambda: False)
    plan = build_restore_check_plan(database, require_backup_id=True)
    assert not plan.allowed
    assert any("backup-id" in b.lower() or "backup_id" in b.lower() for b in plan.blockers)


@pytest.mark.parametrize("partial", [{"backup_kind": "schema_only"}, {"object_scope": "tables_only"}])
def test_partial_backup_cannot_shadow_full_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: dict[str, str]
) -> None:
    from mercury.backup import health

    database = "erebus_threat_intel_prod"
    root = tmp_path / "mercury_backups"
    now = datetime.now(UTC)
    earlier = now - timedelta(hours=1)
    older = _write_backup(
        root, database=database, backup_id="older-full", stamp="older",
        day=earlier.date().isoformat(), created_at=earlier.isoformat(),
    )
    newer = _write_backup(
        root, database=database, backup_id="newer-partial", stamp="newer",
        day=now.date().isoformat(), created_at=now.isoformat(), verified_manifest=True,
    )
    manifest_path = newer / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps({**manifest, **partial}))
    policy = ExecutionPolicy(dry_run=True, live_actions_enabled=False, backup_root=root)
    monkeypatch.setattr("mercury.restore.check_plan.load_execution_policy", lambda: policy)
    monkeypatch.setattr("mercury.restore.check_plan.should_probe_database_status", lambda: False)
    monkeypatch.setattr(health, "load_storage_config", lambda: SimpleNamespace(
        active_write_root=SimpleNamespace(mount_path=tmp_path, filesystem_uuid="fixture")
    ))
    monkeypatch.setattr(health, "require_disk", lambda *_: None)
    monkeypatch.setattr(health, "_unit_state", dict)
    monkeypatch.setattr(health, "load_repo_definitions", list)
    monkeypatch.setattr(health, "ACTIVE_BACKUP_SOURCE_DATABASES", [database])

    assert find_latest_backup_directory(root, database) == newer
    assert find_latest_artifact_verified_backup(root, database) == newer
    assert not verify_backup_artifacts(newer, require_complete_full=True).verified
    status = build_backup_status_report(policy=policy, sources_override=[database])
    assert status.entries[0].backup_id == "older-full"
    assert status.verified_count == 1
    candidate, = resolve_deployment_candidates(policy=policy, databases=[database])
    assert candidate.backup_id == "older-full"
    assert resolve_deployment_candidates(
        policy=policy, databases=[database], backup_ids={database: "newer-partial"}
    ) == []
    assert build_restore_check_plan(database).backup_id == "older-full"
    override = build_restore_check_plan(
        database, backup_directory_override=newer, allow_unverified=True
    )
    assert not override.allowed
    assert any("complete full backup" in issue for issue in override.blockers)

    health_report = health.build_health()
    assert health_report["databases"][database]["backup_id"] == "older-full"
    assert any("no passing verification stamp" in warning for warning in health_report["warnings"])

    (older / "manifest.json").unlink()
    assert build_backup_status_report(policy=policy, sources_override=[database]).missing_count == 1
    health_report = health.build_health()
    assert health_report["databases"][database]["backup_id"] is None
    assert any("missing or stale full" in warning for warning in health_report["warnings"])


def test_conflicting_manifest_policy_fails_verification_and_automatic_selection(tmp_path: Path) -> None:
    database = "erebus_threat_intel_prod"
    older = _write_backup(
        tmp_path, database=database, backup_id="legacy-full", stamp="older",
        day="2026-07-22", created_at="2026-07-22T10:00:00+00:00",
    )
    newer = _write_backup(
        tmp_path, database=database, backup_id="conflicting-full", stamp="newer",
        day="2026-07-22", created_at="2026-07-22T11:00:00+00:00",
    )
    legacy_path = older / "manifest.json"
    legacy = json.loads(legacy_path.read_text())
    del legacy["live_actions_enabled"]
    legacy_path.write_text(json.dumps(legacy))
    manifest_path = newer / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps({**manifest, "live_actions_enabled": False, "verified": True}))

    assert verify_backup_artifacts(older, require_complete_full=True).verified
    result = verify_backup_artifacts(newer, require_complete_full=True)
    assert not result.verified
    assert result.checksum_matches
    assert any("live_actions_enabled=false" in issue for issue in result.issues)
    assert manifest_verified_stamp(manifest_path) is None
    assert resolve_backup_directory(tmp_path, database, require_complete_full=True) == older


def test_receipt_refuses_inactive_mount(tmp_path: Path) -> None:
    batch = BackupBatchResult(
        backup_kind=BACKUP_KIND_FULL,
        execute=True,
        sources=["android_permission_intel"],
        results=[_executed("android_permission_intel", "android_permission_intel-full-1", tmp_path / "b")],
        executed_count=1,
    )
    (tmp_path / "b").mkdir()
    result = build_full_backup_run_result(
        run_id="run1",
        started_at_utc="2026-07-22T00:00:00+00:00",
        production_batch=batch,
        production_verification=BatchVerificationSummary(
            verified=1, backup_ids=["android_permission_intel-full-1"]
        ),
    )
    mount = tmp_path / "shadow"
    mount.mkdir()
    control = mount / ".mercury_control"
    with patch("mercury.core.usb_mount.resolve_operator_mount", return_value=mount):
        with patch("mercury.core.usb_mount.usb_mount_is_active", return_value=False):
            with pytest.raises(ValueError, match="not active"):
                write_full_backup_run_receipt(
                    result, require_active_operator_mount=True, control_root=control
                )


def test_receipt_atomic_sidecar_and_partial_without_receipt(tmp_path: Path) -> None:
    batch = BackupBatchResult(
        backup_kind=BACKUP_KIND_FULL,
        execute=True,
        sources=["android_permission_intel"],
        results=[_executed("android_permission_intel", "android_permission_intel-full-1", tmp_path / "b")],
        executed_count=1,
    )
    (tmp_path / "b").mkdir()
    result = build_full_backup_run_result(
        run_id="run2",
        started_at_utc="2026-07-22T00:00:00+00:00",
        production_batch=batch,
        production_verification=BatchVerificationSummary(
            verified=1, backup_ids=["android_permission_intel-full-1"]
        ),
    )
    assert result.outcome == FullBackupOutcome.PASS
    control = tmp_path / ".mercury_control"
    path = write_full_backup_run_receipt(result, control_root=control)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    assert path.is_file()
    assert sidecar.is_file()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest in sidecar.read_text(encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["production"]["backup_ids"] == ["android_permission_intel-full-1"]

    sealed = apply_full_backup_run_evidence(result, receipt_path=path)
    assert sealed.run_evidence_result == LaneResult.PASS
    assert sealed.outcome == FullBackupOutcome.PASS

    missing = apply_full_backup_run_evidence(result, receipt_path=None, receipt_error="forced")
    assert missing.outcome == FullBackupOutcome.PARTIAL
    assert missing.run_evidence_result == LaneResult.FAIL
