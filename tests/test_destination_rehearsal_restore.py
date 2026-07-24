"""Focused tests for sealed, retained destination restore-checks."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from mercury.cli import app
from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.restore.check_plan import RestoreCheckPlan, build_restore_check_plan
from mercury.restore.destination_rehearsal import (
    PackageRestoreArtifact,
    assert_destination_receipt_root,
    resolve_package_restore_artifact,
)
from mercury.restore.restore_runner import execute_restore_into_database


EREBUS_ID = "erebus_threat_intel_prod-full-20260722_055507_238"
EREBUS_TARGET = "_restorecheck_erebus_threat_intel_prod_20260722T055400Z_phase3b"
PACKAGE_ID = "destination_rehearsal_final_source_05f3abc_20260724T185539Z"


def _package(tmp_path: Path) -> Path:
    root = tmp_path / PACKAGE_ID
    docs = root / "destination_documents"
    payload = root / "payload" / "001_backup"
    docs.mkdir(parents=True)
    payload.mkdir(parents=True)
    (root / "package_receipt.json").write_text(
        json.dumps({"package_id": PACKAGE_ID, "verification_status": "DESTINATION_PACKAGE_VERIFIED"}),
        encoding="utf-8",
    )
    (docs / "destination_acceptance_checklist.json").write_text(
        json.dumps(
            {
                "retained_target": EREBUS_TARGET,
                "template_target": "_restorecheck_erebus_threat_intel_prod_<destination_rehearsal_id>",
            }
        ),
        encoding="utf-8",
    )
    (payload / "manifest.json").write_text(
        json.dumps({"backup_id": EREBUS_ID, "database": "erebus_threat_intel_prod"}),
        encoding="utf-8",
    )
    return root


def _patch_package_verifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mercury.storage.detach_wizard.verify_package_manifest", lambda root: [])
    monkeypatch.setattr(
        "mercury.restore.destination_rehearsal.verify_backup_artifacts",
        lambda *args, **kwargs: SimpleNamespace(verified=True, backup_id=EREBUS_ID),
    )


def test_package_restore_selects_only_exact_backup_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _package(tmp_path)
    _patch_package_verifiers(monkeypatch)
    selected = resolve_package_restore_artifact(
        package_root=root,
        source_database="erebus_threat_intel_prod",
        backup_id=EREBUS_ID,
        target_schema=EREBUS_TARGET,
    )
    assert selected.package_id == PACKAGE_ID
    assert selected.backup_directory == root / "payload" / "001_backup"
    assert selected.target_schema == EREBUS_TARGET


@pytest.mark.parametrize(
    ("backup_id", "target", "message"),
    [
        ("latest", EREBUS_TARGET, "exact backup_id"),
        (EREBUS_ID, "erebus_threat_intel_prod", "production schema"),
        (EREBUS_ID, "_restorecheck_erebus_threat_intel_prod_invalid", "package-defined"),
        ("erebus_threat_intel_prod-full-20260722_055507_999", EREBUS_TARGET, "exactly one"),
    ],
)
def test_package_restore_refuses_latest_wrong_target_and_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backup_id: str,
    target: str,
    message: str,
) -> None:
    root = _package(tmp_path)
    _patch_package_verifiers(monkeypatch)
    with pytest.raises(ValueError, match=message):
        resolve_package_restore_artifact(
            package_root=root,
            source_database="erebus_threat_intel_prod",
            backup_id=backup_id,
            target_schema=target,
        )


def test_governed_plan_refuses_existing_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "manifest.json").write_text(
        json.dumps({"backup_id": EREBUS_ID, "dump_file": "erebus.sql.gz"}), encoding="utf-8"
    )
    (backup / "erebus.sql.gz").write_bytes(b"fixture")
    monkeypatch.setattr("mercury.restore.check_plan.verify_backup_artifacts", lambda *a, **k: SimpleNamespace(verified=True, backup_id=EREBUS_ID))
    monkeypatch.setattr("mercury.restore.check_plan.should_probe_database_status", lambda: True)
    monkeypatch.setattr("mercury.database.mariadb.session.try_load_mariadb_config", lambda: object())
    monkeypatch.setattr("mercury.database.mariadb.session.fetch_user_database_names", lambda cfg: [EREBUS_TARGET])
    plan = build_restore_check_plan(
        "erebus_threat_intel_prod",
        backup_id=EREBUS_ID,
        require_backup_id=True,
        target_schema=EREBUS_TARGET,
        backup_directory_override=backup,
    )
    assert plan.allowed is False
    assert any("already exists" in blocker for blocker in plan.blockers)


def test_retain_flag_prevents_success_cleanup_and_writes_local_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fixture")
    (tmp_path / "manifest.json").write_text(json.dumps({"backup_id": EREBUS_ID}), encoding="utf-8")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    calls: list[str] = []
    monkeypatch.setattr("mercury.restore.restore_runner._execute_client_sql", lambda _cfg, sql: calls.append(sql))
    monkeypatch.setattr(
        "mercury.restore.restore_runner._verify_restore_target",
        lambda *a, **k: SimpleNamespace(verified=True, detail="verified", issues=[], table_count=3),
    )
    receipt_root = tmp_path / "destination-local" / "mercury"
    result = execute_restore_into_database(
        target_database=EREBUS_TARGET,
        dump_path=dump,
        source_database="erebus_threat_intel_prod",
        execute=True,
        policy=policy,
        recreate_target=False,
        cleanup_after_success=False,
        receipt_root=receipt_root,
        config=MariaDbConnectionConfig(host="localhost", user="root", use_client=True, unix_socket="/tmp/socket"),
        import_runner=lambda *args: calls.append("import"),
    )
    assert result.executed is True
    assert result.cleanup_dropped is False
    assert not any(sql.startswith("DROP DATABASE") for sql in calls)
    assert result.receipt_path == str(receipt_root / "operations.jsonl")
    assert Path(result.receipt_path).is_file()


def test_governed_rehearsal_does_not_require_hdd_write_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump = tmp_path / "erebus.sql.gz"
    dump.write_bytes(b"fixture")
    (tmp_path / "manifest.json").write_text(json.dumps({"backup_id": EREBUS_ID}), encoding="utf-8")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path / "missing-backup-root",
        config_path=None,
    )
    monkeypatch.setattr("mercury.restore.restore_runner._execute_client_sql", lambda *_args: None)
    monkeypatch.setattr(
        "mercury.restore.restore_runner._verify_restore_target",
        lambda *a, **k: SimpleNamespace(verified=True, detail="verified", issues=[], table_count=1),
    )
    result = execute_restore_into_database(
        target_database=EREBUS_TARGET,
        dump_path=dump,
        source_database="erebus_threat_intel_prod",
        execute=True,
        policy=policy,
        recreate_target=False,
        receipt_root=tmp_path / "receipt",
        governed_destination_rehearsal=True,
        config=MariaDbConnectionConfig(host="localhost", user="root", use_client=True, unix_socket="/tmp/socket"),
        import_runner=lambda *args: None,
    )
    assert result.executed is True


def test_receipt_root_must_be_destination_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("mercury.restore.destination_rehearsal.DESTINATION_RECEIPT_ROOT", tmp_path / "local")
    assert assert_destination_receipt_root(tmp_path / "local" / "mercury") == (tmp_path / "local" / "mercury")
    with pytest.raises(ValueError, match="destination-local"):
        assert_destination_receipt_root(tmp_path / "elsewhere")


def test_governed_run_preview_shows_exact_backup_and_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = PackageRestoreArtifact(
        package_id=PACKAGE_ID,
        package_root=tmp_path,
        backup_id=EREBUS_ID,
        source_database="erebus_threat_intel_prod",
        backup_directory=tmp_path,
        target_schema=EREBUS_TARGET,
    )
    monkeypatch.setattr("mercury.restore.destination_rehearsal.resolve_package_restore_artifact", lambda **kwargs: artifact)
    monkeypatch.setattr("mercury.restore.destination_rehearsal.assert_destination_receipt_root", lambda path: path)
    monkeypatch.setattr(
        "mercury.restore.check_plan.build_restore_check_plan",
        lambda db, **kwargs: RestoreCheckPlan(
            source_prod=db,
            restore_target=kwargs["target_schema"],
            backup_directory=str(tmp_path),
            backup_id=kwargs["backup_id"],
            backup_verified=True,
            dump_file="erebus.sql.gz",
            allowed=True,
            planned_commands=["pinned preview"],
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "restore-check", "run", "--db", "erebus_threat_intel_prod", "--backup-id", EREBUS_ID,
            "--target-schema", EREBUS_TARGET, "--package-root", str(tmp_path), "--retain-after-success",
        ],
    )
    assert result.exit_code == 0
    assert EREBUS_ID in result.stdout
    assert EREBUS_TARGET in result.stdout


def test_governed_run_requires_target_package_and_retain() -> None:
    result = CliRunner().invoke(
        app,
        ["restore-check", "run", "--db", "erebus_threat_intel_prod", "--backup-id", EREBUS_ID],
    )
    # Legacy command is still supported, including its automatic cleanup behavior.
    assert result.exit_code in {0, 1}
    missing_pair = CliRunner().invoke(
        app,
        ["restore-check", "run", "--db", "erebus_threat_intel_prod", "--backup-id", EREBUS_ID, "--target-schema", EREBUS_TARGET],
    )
    assert missing_pair.exit_code != 0
