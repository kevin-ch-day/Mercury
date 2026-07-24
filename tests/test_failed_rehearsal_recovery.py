"""Synthetic coverage for the one-target failed rehearsal recovery lane."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.restore.restore_runner import RestoreExecutionResult


EREBUS_ID = "erebus_threat_intel_prod-full-20260722_055507_238"
ANDROID_ID = "android_permission_intel-full-20260722_055648_287"
EREBUS_TARGET = "_restorecheck_erebus_threat_intel_prod_20260722T055400Z_phase3b"
ANDROID_TARGET = "_restorecheck_android_permission_intel_20260722T055400Z_phase3b"
PACKAGE_ID = "destination_rehearsal_final_source_05f3abc_20260724T185539Z"
E_COUNTS = {"table_count": 125, "view_count": 76, "trigger_count": 15, "routine_count": 7, "event_count": 0}
A_COUNTS = {"table_count": 41, "view_count": 35, "trigger_count": 24, "routine_count": 0, "event_count": 0}


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / PACKAGE_ID
    receipt_root = tmp_path / "local" / "mercury"
    (root / "destination_documents").mkdir(parents=True)
    for ordinal, backup_id, database in (("001", EREBUS_ID, "erebus_threat_intel_prod"), ("002", ANDROID_ID, "android_permission_intel")):
        artifact = root / "payload" / f"{ordinal}_backup" 
        artifact.mkdir(parents=True)
        (artifact / "manifest.json").write_text(json.dumps({"backup_id": backup_id, "database": database, "dump_file": "dump.sql.gz"}), encoding="utf-8")
        (artifact / "dump.sql.gz").write_bytes(b"fixture")
    phase = root / "payload" / "000_phase3b_run_20260722T055400Z_phase3b" / "restore"
    phase.mkdir(parents=True)
    (phase / "source_vs_restore_comparison.json").write_text(
        json.dumps({"restore_checkpoints": {"erebus": E_COUNTS, "android": A_COUNTS}}), encoding="utf-8"
    )
    (root / "package_receipt.json").write_text(json.dumps({"package_id": PACKAGE_ID, "verification_status": "DESTINATION_PACKAGE_VERIFIED"}), encoding="utf-8")
    (root / "destination_documents" / "destination_acceptance_checklist.json").write_text(
        json.dumps({"erebus": EREBUS_TARGET, "android": ANDROID_TARGET}), encoding="utf-8"
    )
    receipt_root.mkdir(parents=True)
    rows = [
        {"event_type": "restore_check_failed", "backup_id": EREBUS_ID, "target_database": EREBUS_TARGET, "backup_directory_path": str(root / "payload" / "001_backup")},
        {"event_type": "restore_check_passed", "backup_id": ANDROID_ID, "target_database": ANDROID_TARGET, "backup_directory_path": str(root / "payload" / "002_backup")},
    ]
    (receipt_root / "operations.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr("mercury.restore.destination_rehearsal.DESTINATION_RECEIPT_ROOT", tmp_path / "local")
    monkeypatch.setattr("mercury.storage.detach_wizard.verify_package_manifest", lambda root: [])
    monkeypatch.setattr(
        "mercury.restore.destination_rehearsal.verify_backup_artifacts",
        lambda path, **kwargs: type("Verified", (), {"verified": True, "backup_id": json.loads((path / "manifest.json").read_text())["backup_id"]})(),
    )
    return root, receipt_root


def _context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from mercury.restore.failed_rehearsal_recovery import build_failed_rehearsal_recovery

    root, receipt_root = _fixture(tmp_path, monkeypatch)
    return build_failed_rehearsal_recovery(
        package_root=root, erebus_backup_id=EREBUS_ID, erebus_target=EREBUS_TARGET,
        android_backup_id=ANDROID_ID, android_target=ANDROID_TARGET, receipt_root=receipt_root,
    )


def test_recovery_context_derives_exact_two_schema_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    assert context.schema_map == {"erebus_threat_intel_prod": EREBUS_TARGET, "android_permission_intel": ANDROID_TARGET}
    assert context.failed_receipt["event_type"] == "restore_check_failed"
    assert context.android_receipt["event_type"] == "restore_check_passed"


def test_recovery_refuses_wrong_or_successful_failed_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, receipt_root = _fixture(tmp_path, monkeypatch)
    (receipt_root / "operations.jsonl").write_text(
        json.dumps({"event_type": "restore_check_passed", "backup_id": EREBUS_ID, "target_database": EREBUS_TARGET, "backup_directory_path": str(root / "payload" / "001_backup")}) + "\n",
        encoding="utf-8",
    )
    from mercury.restore.failed_rehearsal_recovery import build_failed_rehearsal_recovery

    with pytest.raises(ValueError, match="restore_check_failed"):
        build_failed_rehearsal_recovery(package_root=root, erebus_backup_id=EREBUS_ID, erebus_target=EREBUS_TARGET, android_backup_id=ANDROID_ID, android_target=ANDROID_TARGET, receipt_root=receipt_root)


def test_recovery_preflight_protects_android_and_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    import mercury.restore.failed_rehearsal_recovery as recovery

    monkeypatch.setattr(recovery, "fetch_user_database_names", lambda cfg: [EREBUS_TARGET, ANDROID_TARGET])
    monkeypatch.setattr(recovery, "schema_object_counts", lambda cfg, schema: A_COUNTS if schema == ANDROID_TARGET else {**E_COUNTS, "view_count": 75})
    assert recovery.validate_failed_rehearsal_recovery(context, config=object())["android"] == A_COUNTS
    monkeypatch.setattr(recovery, "fetch_user_database_names", lambda cfg: [EREBUS_TARGET, ANDROID_TARGET, "erebus_threat_intel_prod"])
    with pytest.raises(ValueError, match="Production schemas"):
        recovery.validate_failed_rehearsal_recovery(context, config=object())


def test_recovery_replaces_only_failed_target_and_writes_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    import mercury.restore.failed_rehearsal_recovery as recovery

    calls: list[dict] = []
    monkeypatch.setattr(recovery, "fetch_user_database_names", lambda cfg: [EREBUS_TARGET, ANDROID_TARGET])
    state = {"recovered": False}
    monkeypatch.setattr(recovery, "schema_object_counts", lambda cfg, schema: A_COUNTS if schema == ANDROID_TARGET else (E_COUNTS if state["recovered"] else {**E_COUNTS, "view_count": 75}))

    def fake_restore(**kwargs):
        calls.append(kwargs)
        state["recovered"] = True
        return RestoreExecutionResult(source_database="erebus_threat_intel_prod", target_database=EREBUS_TARGET, dump_path="dump", dry_run=False, executed=True, verification_passed=True)

    monkeypatch.setattr(recovery, "execute_restore_into_database", fake_restore)
    result, receipt = recovery.execute_failed_rehearsal_recovery(context, config=object())
    assert calls[0]["target_database"] == EREBUS_TARGET
    assert calls[0]["recreate_target"] is True
    assert calls[0]["schema_rewrites"] == context.schema_map
    assert ANDROID_TARGET not in " ".join(calls[0]["commands"] if "commands" in calls[0] else [])
    assert result["final_decision"] == "FAILED_RETAINED_TARGET_REPLACED_AND_VERIFIED"
    assert receipt.is_file()
    assert list((context.receipt_root / "recovery_receipts").glob("*_authorization.json"))
