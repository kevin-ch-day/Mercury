"""Tests for the narrow five-schema destination package restore lane."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.restore.destination_recovery import (
    build_destination_recovery_plan,
    execute_destination_recovery,
    verify_existing_destination_recovery,
)


PACKAGE_ID = "destination_rehearsal_fixture_20260725T000000Z"
SCHEMA = "android_permission_intel_dev"
BACKUP_ID = "android_permission_intel_dev-full-20260722_161819_199"


def _package(tmp_path: Path) -> Path:
    root = tmp_path / PACKAGE_ID
    payload = root / "payload" / "005_backup"
    payload.mkdir(parents=True)
    (root / "package_receipt.json").write_text(
        json.dumps({"package_id": PACKAGE_ID, "verification_status": "DESTINATION_PACKAGE_VERIFIED"}),
        encoding="utf-8",
    )
    (root / "package_manifest.json").write_text(
        json.dumps({"members": [{"kind": "backup", "identity": BACKUP_ID, "package_relative": "payload/005_backup"}]}),
        encoding="utf-8",
    )
    (payload / "manifest.json").write_text(
        json.dumps({"database": SCHEMA, "backup_id": BACKUP_ID, "dump_file": "dump.sql.gz"}),
        encoding="utf-8",
    )
    (payload / "dump.sql.gz").write_bytes(b"fixture")
    return root


def _patch_verifiers(monkeypatch: pytest.MonkeyPatch, *, existing: list[str] | None = None) -> None:
    monkeypatch.setattr("mercury.storage.detach_wizard.verify_package_manifest", lambda root: [])
    monkeypatch.setattr(
        "mercury.restore.destination_recovery.verify_backup_artifacts",
        lambda *args, **kwargs: SimpleNamespace(verified=True, backup_id=BACKUP_ID),
    )
    monkeypatch.setattr(
        "mercury.restore.destination_recovery.fetch_user_database_names",
        lambda _config: existing or [],
    )


def test_exact_verified_package_and_backup_are_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _package(tmp_path)
    _patch_verifiers(monkeypatch)
    plan = build_destination_recovery_plan(
        package_root=root, package_id=PACKAGE_ID, source_schema=SCHEMA,
        target_schema=SCHEMA, backup_id=BACKUP_ID,
        config=MariaDbConnectionConfig(host="localhost", user="root"),
    )
    assert plan.allowed
    assert plan.backup_directory == root / "payload" / "005_backup"
    with pytest.raises(ValueError, match="latest"):
        build_destination_recovery_plan(
            package_root=root, package_id=PACKAGE_ID, source_schema=SCHEMA,
            target_schema=SCHEMA, backup_id="latest",
            config=MariaDbConnectionConfig(host="localhost", user="root"),
        )


def test_existing_or_unapproved_targets_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _package(tmp_path)
    _patch_verifiers(monkeypatch, existing=[SCHEMA])
    plan = build_destination_recovery_plan(
        package_root=root, package_id=PACKAGE_ID, source_schema=SCHEMA,
        target_schema=SCHEMA, backup_id=BACKUP_ID,
        config=MariaDbConnectionConfig(host="localhost", user="root"),
    )
    assert not plan.allowed
    assert "already exists" in plan.blockers[0]
    with pytest.raises(ValueError, match="approved"):
        build_destination_recovery_plan(
            package_root=root, package_id=PACKAGE_ID, source_schema="unknown_prod",
            target_schema="unknown_prod", backup_id=BACKUP_ID,
            config=MariaDbConnectionConfig(host="localhost", user="root"),
        )


def test_execution_uses_governed_recovery_and_destination_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package(tmp_path)
    _patch_verifiers(monkeypatch)
    config = MariaDbConnectionConfig(host="localhost", user="root")
    plan = build_destination_recovery_plan(
        package_root=root, package_id=PACKAGE_ID, source_schema=SCHEMA,
        target_schema=SCHEMA, backup_id=BACKUP_ID, config=config,
    )
    calls: dict = {}
    monkeypatch.setattr(
        "mercury.restore.destination_recovery.assert_destination_receipt_root",
        lambda path: path,
    )
    def fake_restore(**kwargs):
        calls["kwargs"] = kwargs
        return SimpleNamespace(executed=True, verification_passed=True, receipt_path="receipt")

    monkeypatch.setattr("mercury.restore.destination_recovery.execute_restore_into_database", fake_restore)
    monkeypatch.setattr(
        "mercury.restore.destination_recovery.inspect_database_on_server",
        lambda *_args: SimpleNamespace(
            exists_on_server=True, connected=True, table_count=1, view_count=0,
        ),
    )
    result, inspect = execute_destination_recovery(plan, config=config, receipt_root=tmp_path)
    assert inspect.table_count == 1
    assert calls["kwargs"]["governed_destination_recovery"] is True
    assert calls["kwargs"]["rollback_new_target_on_failure"] is True
    receipt = Path(result.receipt_path)
    assert receipt.is_file()
    recorded = json.loads(receipt.read_text(encoding="utf-8"))
    assert recorded["decision"] == "VERIFIED"
    assert recorded["package_id"] == PACKAGE_ID
    assert recorded["backup_id"] == BACKUP_ID


def test_existing_target_verification_is_read_only_and_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package(tmp_path)
    _patch_verifiers(monkeypatch, existing=[SCHEMA])
    config = MariaDbConnectionConfig(host="localhost", user="root")
    plan = build_destination_recovery_plan(
        package_root=root, package_id=PACKAGE_ID, source_schema=SCHEMA,
        target_schema=SCHEMA, backup_id=BACKUP_ID, config=config, allow_existing_target=True,
    )
    monkeypatch.setattr("mercury.restore.destination_recovery.assert_destination_receipt_root", lambda path: path)
    monkeypatch.setattr(
        "mercury.restore.destination_recovery.inspect_database_on_server",
        lambda *_args: SimpleNamespace(exists_on_server=True, connected=True, table_count=1, view_count=0),
    )
    monkeypatch.setattr(
        "mercury.deploy.verification.verify_deployed_database",
        lambda *_args, **_kwargs: SimpleNamespace(verified=True, detail="ok"),
    )
    receipt, inspect = verify_existing_destination_recovery(plan, config=config, receipt_root=tmp_path)
    assert inspect.table_count == 1
    recorded = json.loads(receipt.read_text(encoding="utf-8"))
    assert recorded["decision"] == "EXISTING_TARGET_VERIFIED"
