"""Synthetic coverage for the sealed-package production-cutover lane."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.restore.restore_runner import RestoreExecutionResult


PACKAGE_ID = "destination_rehearsal_final_source_05f3abc_20260724T185539Z"
ANDROID_ID = "android_permission_intel-full-20260722_055648_287"
EREBUS_ID = "erebus_threat_intel_prod-full-20260722_055507_238"
ANDROID_REHEARSAL = "_restorecheck_android_permission_intel_20260722T055400Z_phase3b"
EREBUS_REHEARSAL = "_restorecheck_erebus_threat_intel_prod_20260722T055400Z_phase3b"
A_COUNTS = {"table_count": 41, "view_count": 35, "trigger_count": 24, "routine_count": 0, "event_count": 0}
E_COUNTS = {"table_count": 125, "view_count": 76, "trigger_count": 15, "routine_count": 7, "event_count": 0}


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / PACKAGE_ID
    receipt_root = tmp_path / "local" / "mercury"
    (root / "destination_documents").mkdir(parents=True)
    (root / "package_receipt.json").write_text(
        json.dumps({"package_id": PACKAGE_ID, "verification_status": "DESTINATION_PACKAGE_VERIFIED"}), encoding="utf-8"
    )
    (root / "destination_documents" / "destination_acceptance_checklist.json").write_text(
        json.dumps({"android": ANDROID_REHEARSAL, "erebus": EREBUS_REHEARSAL}), encoding="utf-8"
    )
    for ordinal, database, backup_id in (("001", "erebus_threat_intel_prod", EREBUS_ID), ("002", "android_permission_intel", ANDROID_ID)):
        artifact = root / "payload" / f"{ordinal}_backup"
        artifact.mkdir(parents=True)
        (artifact / "dump.sql.gz").write_bytes(b"fixture dump")
        (artifact / "manifest.json").write_text(
            json.dumps({"database": database, "backup_id": backup_id, "dump_file": "dump.sql.gz"}), encoding="utf-8"
        )
    phase = root / "payload" / "000_phase3b_run_20260722T055400Z_phase3b" / "restore"
    phase.mkdir(parents=True)
    (phase / "source_vs_restore_comparison.json").write_text(
        json.dumps({"restore_checkpoints": {"android": A_COUNTS, "erebus": E_COUNTS}}), encoding="utf-8"
    )
    capture = root / "payload" / "003_erebus_capture_fixture"
    capture.mkdir(parents=True)
    (capture / "capture_summary.json").write_text(
        json.dumps({"capture_id": "erebus_destination_candidate_05f3abc_20260724T185539Z", "status": "CAPTURE_VERIFIED", "active_authority": True, "historical_only": False}), encoding="utf-8"
    )
    receipt_root.mkdir(parents=True)
    rows = [
        {"event_type": "restore_check_passed", "backup_id": ANDROID_ID, "target_database": ANDROID_REHEARSAL, "backup_directory_path": str(root / "payload" / "002_backup")},
    ]
    (receipt_root / "operations.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    recovery = receipt_root / "recovery_receipts"
    recovery.mkdir()
    (recovery / "ok_result.json").write_text(json.dumps({
        "package_id": PACKAGE_ID, "erebus_backup_id": EREBUS_ID, "failed_erebus_target": EREBUS_REHEARSAL,
        "final_decision": "FAILED_RETAINED_TARGET_REPLACED_AND_VERIFIED",
    }), encoding="utf-8")
    monkeypatch.setattr("mercury.restore.destination_rehearsal.DESTINATION_RECEIPT_ROOT", tmp_path / "local")
    monkeypatch.setattr("mercury.storage.detach_wizard.verify_package_manifest", lambda _root: [])
    monkeypatch.setattr(
        "mercury.restore.destination_rehearsal.verify_backup_artifacts",
        lambda path, **_kwargs: SimpleNamespace(verified=True, backup_id=json.loads((path / "manifest.json").read_text())["backup_id"]),
    )
    return root, receipt_root


def _context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from mercury.restore.production_cutover import build_production_cutover_context

    root, receipt_root = _fixture(tmp_path, monkeypatch)
    return build_production_cutover_context(
        package_root=root, package_id=PACKAGE_ID, android_backup_id=ANDROID_ID, erebus_backup_id=EREBUS_ID,
        android_source_schema=ANDROID_REHEARSAL, erebus_source_schema=EREBUS_REHEARSAL,
        android_target_schema="android_permission_intel", erebus_target_schema="erebus_threat_intel_prod",
        receipt_root=receipt_root,
    )


def _preflight_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    import mercury.restore.production_cutover as cutover

    monkeypatch.setattr(cutover, "fetch_user_database_names", lambda _cfg: [ANDROID_REHEARSAL, EREBUS_REHEARSAL])
    monkeypatch.setattr(cutover, "schema_object_counts", lambda _cfg, schema: A_COUNTS if schema == ANDROID_REHEARSAL else E_COUNTS)
    monkeypatch.setattr(cutover, "_metadata_reference_count", lambda *_args: 0)
    monkeypatch.setattr(cutover, "_mount_options", lambda _path: "ro,nosuid,nodev")
    monkeypatch.setattr(cutover, "_writers_active", lambda: [])
    monkeypatch.setattr(cutover, "_run_rehearsal_smoke", lambda _ctx: {"passed": True, "summary": "29 exact comparisons"})
    monkeypatch.setattr(cutover, "_git_identity", lambda: {"mercury_commit": "m", "erebus_commit": "e", "erebus_tree": "t"})
    monkeypatch.setattr(cutover, "inspect_production_privileges", lambda _cfg: {"passed": True, "account": "systemadmin@localhost"})


def _config() -> MariaDbConnectionConfig:
    return MariaDbConnectionConfig(host="127.0.0.1", user="systemadmin", use_client=True, unix_socket="/tmp/mysql.sock")


def _grant_lines(*, android: set[str] | None = None, erebus: set[str] | None = None, account: str = "`systemadmin`@`localhost`") -> list[str]:
    from mercury.restore.production_cutover import required_production_privileges

    required = required_production_privileges()
    android = android if android is not None else set(required["android_permission_intel"])
    erebus = erebus if erebus is not None else set(required["erebus_threat_intel_prod"])
    return [
        f"GRANT CREATE ON *.* TO {account}",
        f"GRANT {', '.join(sorted(android))} ON `android_permission_intel`.* TO {account}",
        f"GRANT {', '.join(sorted(erebus))} ON `erebus_threat_intel_prod`.* TO {account}",
    ]


def test_context_requires_exact_ids_schemas_targets_and_maps_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    assert context.schema_map == {"android_permission_intel": "android_permission_intel", "erebus_threat_intel_prod": "erebus_threat_intel_prod"}
    from mercury.restore.production_cutover import build_production_cutover_context
    root, receipts = _fixture(tmp_path / "second", monkeypatch)
    with pytest.raises(ValueError, match="latest"):
        build_production_cutover_context(
            package_root=root, package_id=PACKAGE_ID, android_backup_id="latest", erebus_backup_id=EREBUS_ID,
            android_source_schema=ANDROID_REHEARSAL, erebus_source_schema=EREBUS_REHEARSAL,
            android_target_schema="android_permission_intel", erebus_target_schema="erebus_threat_intel_prod", receipt_root=receipts,
        )
    assert context.android.target_schema == ANDROID_REHEARSAL


def test_preflight_refuses_collision_remote_writer_and_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _preflight_patches(monkeypatch)
    import mercury.restore.production_cutover as cutover

    assert cutover.validate_production_cutover_preflight(context, config=_config())["collision_result"] == "absent"
    monkeypatch.setattr(cutover, "fetch_user_database_names", lambda _cfg: [ANDROID_REHEARSAL, EREBUS_REHEARSAL, "android_permission_intel"])
    with pytest.raises(ValueError, match="collision"):
        cutover.validate_production_cutover_preflight(context, config=_config())
    monkeypatch.setattr(cutover, "fetch_user_database_names", lambda _cfg: [ANDROID_REHEARSAL, EREBUS_REHEARSAL])
    with pytest.raises(ValueError, match="loopback"):
        cutover.validate_production_cutover_preflight(context, config=MariaDbConnectionConfig(host="db.example", user="x", unix_socket="/tmp/s"))
    with pytest.raises(ValueError, match="writers"):
        cutover.validate_production_cutover_preflight(context, config=_config(), writer_probe=lambda: ["erebus worker"])


@pytest.mark.parametrize("mount_options", ["ro", "ro,nosuid", "ro,nodev", "rw,nosuid,nodev"])
def test_preflight_requires_sealed_read_only_mount_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mount_options: str,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _preflight_patches(monkeypatch)
    import mercury.restore.production_cutover as cutover

    with pytest.raises(ValueError, match="ro,nosuid,nodev"):
        cutover.validate_production_cutover_preflight(
            context, config=_config(), mount_options=lambda _path: mount_options,
        )


def test_preview_is_read_only_and_writes_only_local_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _preflight_patches(monkeypatch)
    import mercury.restore.production_cutover as cutover

    preview, path = cutover.create_production_cutover_preview(context, config=_config())
    assert preview["preview_decision"] == cutover.PREVIEW_APPROVED
    assert preview["database_writes"] == 0
    assert preview["preflight"]["privilege_preflight"]["passed"] is True
    assert path.is_file() and context.receipt_root in path.parents


def test_privilege_preflight_requires_exact_local_schema_capabilities() -> None:
    from mercury.restore.production_cutover import inspect_production_privileges, required_production_privileges

    decision = inspect_production_privileges(_config(), grant_lines=_grant_lines())
    assert decision["passed"] is True
    required = required_production_privileges()
    for capability in ("DROP", "CREATE VIEW", "TRIGGER"):
        android = set(required["android_permission_intel"])
        android.remove(capability)
        with pytest.raises(ValueError, match=capability):
            inspect_production_privileges(_config(), grant_lines=_grant_lines(android=android))
    erebus = set(required["erebus_threat_intel_prod"])
    erebus.remove("CREATE ROUTINE")
    with pytest.raises(ValueError, match="CREATE ROUTINE"):
        inspect_production_privileges(_config(), grant_lines=_grant_lines(erebus=erebus))
    rehearsal_only = [
        "GRANT CREATE ON *.* TO `systemadmin`@`localhost`",
        "GRANT ALL PRIVILEGES ON `_restorecheck_android_permission_intel_20260722T055400Z_phase3b`.* TO `systemadmin`@`localhost`",
    ]
    with pytest.raises(ValueError, match="android_permission_intel"):
        inspect_production_privileges(_config(), grant_lines=rehearsal_only)
    with pytest.raises(ValueError, match="global CREATE"):
        inspect_production_privileges(_config(), grant_lines=_grant_lines()[1:])
    remote = _grant_lines(account="`systemadmin`@`%`")
    with pytest.raises(ValueError, match="global CREATE"):
        inspect_production_privileges(_config(), grant_lines=remote)


def test_execute_requires_confirmation_orders_mapping_and_consumes_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _preflight_patches(monkeypatch)
    import mercury.restore.production_cutover as cutover

    _preview, preview_path = cutover.create_production_cutover_preview(context, config=_config())
    with pytest.raises(ValueError, match="confirmation"):
        cutover.execute_production_cutover(context, preview_receipt=preview_path, confirmation="no", config=_config())
    calls: list[dict] = []
    counts = {"android_permission_intel": A_COUNTS, "erebus_threat_intel_prod": E_COUNTS}
    monkeypatch.setattr(cutover, "schema_object_counts", lambda _cfg, schema: counts.get(schema, A_COUNTS if schema == ANDROID_REHEARSAL else E_COUNTS))
    def fake_restore(**kwargs):
        calls.append(kwargs)
        if kwargs["target_database"] == "erebus_threat_intel_prod":
            journal_path = next((context.receipt_root / "production_cutover_receipts").glob("*_in_progress.json"))
            in_progress = json.loads(journal_path.read_text(encoding="utf-8"))
            assert in_progress["journal_state"] == "in_progress"
            assert in_progress["created_targets"] == ["android_permission_intel"]
        kwargs["on_target_created"](kwargs["target_database"])
        return RestoreExecutionResult(source_database=kwargs["source_database"], target_database=kwargs["target_database"], dump_path="x", dry_run=False, executed=True, verification_passed=True)
    result, _path = cutover.execute_production_cutover(
        context, preview_receipt=preview_path, confirmation=cutover.EXECUTE_CONFIRMATION, config=_config(), restore_executor=fake_restore,
    )
    assert result["final_decision"] == "PRODUCTION_CUTOVER_EXECUTED_AND_VALIDATED"
    assert [call["target_database"] for call in calls] == ["android_permission_intel", "erebus_threat_intel_prod"]
    assert all(call["schema_rewrites"] == context.schema_map for call in calls)
    journal = json.loads(Path(result["rollback_journal"]).read_text(encoding="utf-8"))
    assert journal["journal_state"] == "completed"
    assert journal["created_targets"] == ["android_permission_intel", "erebus_threat_intel_prod"]
    assert journal["final_decision"] == "PRODUCTION_CUTOVER_EXECUTED_AND_VALIDATED"
    with pytest.raises(ValueError, match="unconsumed"):
        cutover.execute_production_cutover(context, preview_receipt=preview_path, confirmation=cutover.EXECUTE_CONFIRMATION, config=_config(), restore_executor=fake_restore)


def test_erebus_failure_rolls_back_only_new_production_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _preflight_patches(monkeypatch)
    import mercury.restore.production_cutover as cutover

    _preview, preview_path = cutover.create_production_cutover_preview(context, config=_config())
    calls: list[str] = []
    monkeypatch.setattr(cutover, "schema_object_counts", lambda _cfg, schema: A_COUNTS if "android" in schema else E_COUNTS)
    def fake_restore(**kwargs):
        kwargs["on_target_created"](kwargs["target_database"])
        if kwargs["target_database"] == "erebus_threat_intel_prod":
            return RestoreExecutionResult(source_database="erebus_threat_intel_prod", target_database="erebus_threat_intel_prod", dump_path="x", refused=True, message="broken")
        return RestoreExecutionResult(source_database=ANDROID_ID, target_database="android_permission_intel", dump_path="x", dry_run=False, executed=True, verification_passed=True)
    result, _path = cutover.execute_production_cutover(
        context, preview_receipt=preview_path, confirmation=cutover.EXECUTE_CONFIRMATION, config=_config(), restore_executor=fake_restore,
        sql_executor=lambda _cfg, sql: calls.append(sql),
    )
    assert result["final_decision"] == "PRODUCTION_CUTOVER_ROLLED_BACK"
    assert calls == ["DROP DATABASE IF EXISTS `erebus_threat_intel_prod`", "DROP DATABASE IF EXISTS `android_permission_intel`"]
    assert [event["schema"] for event in result["rollback_events"]] == ["erebus_threat_intel_prod", "android_permission_intel"]


@pytest.mark.parametrize("failure_stage", ["android_import", "android_validation", "erebus_validation"])
def test_partial_creation_and_validation_failures_are_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str,
) -> None:
    context = _context(tmp_path, monkeypatch)
    _preflight_patches(monkeypatch)
    import mercury.restore.production_cutover as cutover

    _preview, preview_path = cutover.create_production_cutover_preview(context, config=_config())
    drops: list[str] = []
    state = {"production_calls": 0}

    def counts(_cfg, schema):
        if failure_stage == "android_validation" and schema == "android_permission_intel":
            return {**A_COUNTS, "view_count": 0}
        if failure_stage == "erebus_validation" and schema == "erebus_threat_intel_prod":
            return {**E_COUNTS, "view_count": 0}
        if schema in {ANDROID_REHEARSAL, "android_permission_intel"}:
            return A_COUNTS
        return E_COUNTS

    monkeypatch.setattr(cutover, "schema_object_counts", counts)

    def fake_restore(**kwargs):
        state["production_calls"] += 1
        kwargs["on_target_created"](kwargs["target_database"])
        if failure_stage == "android_import" and kwargs["target_database"] == "android_permission_intel":
            return RestoreExecutionResult(source_database="android_permission_intel", target_database="android_permission_intel", dump_path="x", refused=True, message="DROP TABLE denied")
        return RestoreExecutionResult(source_database=kwargs["source_database"], target_database=kwargs["target_database"], dump_path="x", dry_run=False, executed=True, verification_passed=True)

    result, _path = cutover.execute_production_cutover(
        context, preview_receipt=preview_path, confirmation=cutover.EXECUTE_CONFIRMATION, config=_config(),
        restore_executor=fake_restore, sql_executor=lambda _cfg, sql: drops.append(sql),
    )
    assert result["final_decision"] == "PRODUCTION_CUTOVER_ROLLED_BACK"
    if failure_stage in {"android_import", "android_validation"}:
        assert drops == ["DROP DATABASE IF EXISTS `android_permission_intel`"]
    else:
        assert drops == ["DROP DATABASE IF EXISTS `erebus_threat_intel_prod`", "DROP DATABASE IF EXISTS `android_permission_intel`"]


def test_rollback_failure_is_recorded_without_hiding_import_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = _context(tmp_path, monkeypatch)
    _preflight_patches(monkeypatch)
    import mercury.restore.production_cutover as cutover

    _preview, preview_path = cutover.create_production_cutover_preview(context, config=_config())
    def fake_restore(**kwargs):
        kwargs["on_target_created"](kwargs["target_database"])
        return RestoreExecutionResult(source_database="android_permission_intel", target_database="android_permission_intel", dump_path="x", refused=True, message="DROP TABLE denied")
    result, _path = cutover.execute_production_cutover(
        context, preview_receipt=preview_path, confirmation=cutover.EXECUTE_CONFIRMATION, config=_config(),
        restore_executor=fake_restore, sql_executor=lambda *_args: (_ for _ in ()).throw(RuntimeError("rollback denied")),
    )
    assert "DROP TABLE denied" in result["failure"]
    assert result["rollback_events"][0]["error"] == "rollback denied"
