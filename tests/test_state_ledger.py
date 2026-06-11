"""Tests for the portable Mercury state ledger."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mercury.backup.backup_runner import execute_backup
from mercury.backup.verification import verify_backup_directory
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.restore.restore_runner import RestoreExecutionResult
from mercury.state.summary import build_state_summary


FIXED_NOW = datetime(2026, 6, 9, 3, 1, 26, tzinfo=timezone.utc)


def _live_policy(tmp_path: Path) -> ExecutionPolicy:
    return ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path / "backups",
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )


def _fake_dump_runner(argv, env, output_path, _config) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"FAKE_GZIP_BACKUP_CONTENT\n")


def _fake_mariadb_config():
    return type(
        "Cfg",
        (),
        {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": None,
            "unix_socket": "/var/lib/mysql/mysql.sock",
            "ssl_disabled": False,
        },
    )()


def test_backup_and_verify_append_state_ledger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setattr("mercury.state.ledger.resolve_state_root", lambda policy=None: state_root)

    result = execute_backup(
        "erebus_threat_intel_prod",
        BACKUP_KIND_FULL,
        execute=True,
        policy=_live_policy(tmp_path),
        date="2026-06-09",
        timestamp="20260609_030126",
        now=FIXED_NOW,
        mariadb_config=_fake_mariadb_config(),
        dump_runner=_fake_dump_runner,
    )
    assert result.executed is True

    verify_result = verify_backup_directory(
        Path(result.backup_directory_path),
        database="erebus_threat_intel_prod",
        update_manifest=True,
    )
    assert verify_result.verified is True

    operations = (state_root / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(operations) == 2
    assert any('"event_type": "backup_executed"' in line for line in operations)
    assert any('"event_type": "backup_verified"' in line for line in operations)

    csv_text = (state_root / "database_backups.csv").read_text(encoding="utf-8")
    assert "erebus_threat_intel_prod" in csv_text
    assert ",backup," in csv_text
    assert ",verify," in csv_text
    assert "True" in csv_text


def test_build_state_summary_counts_files(tmp_path: Path) -> None:
    state_root = tmp_path / "mercury_state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "operations.jsonl").write_text(
        json.dumps({"event_type": "backup_executed"}) + "\n" + json.dumps({"event_type": "backup_verified"}) + "\n",
        encoding="utf-8",
    )
    (state_root / "database_backups.csv").write_text(
        "timestamp,database\n2026-06-09T00:00:00+00:00,android_permission_intel\n",
        encoding="utf-8",
    )
    (state_root / "repo_bundles.csv").write_text(
        "timestamp,repo_name\n2026-06-09T00:00:00+00:00,Mercury\n",
        encoding="utf-8",
    )
    (state_root / "transfer_packages.csv").write_text(
        "timestamp,manifest_path\n2026-06-09T00:00:00+00:00,/tmp/transfer.json\n",
        encoding="utf-8",
    )
    (state_root / "sync_events.csv").write_text(
        "timestamp,source\n2026-06-09T00:00:00+00:00,erebus_threat_intel_prod\n",
        encoding="utf-8",
    )

    summary = build_state_summary(state_root=state_root)
    assert summary.source == "usb"
    assert summary.operations == 2
    assert summary.database_backup_rows == 1
    assert summary.repo_bundle_rows == 1
    assert summary.transfer_package_rows == 1
    assert summary.sync_event_rows == 1


def test_build_state_summary_ignores_pytest_generated_operator_debris(tmp_path: Path) -> None:
    from mercury.state.ledger import (
        DATABASE_BACKUPS_CSV,
        OPERATIONS_JSONL,
        REPO_BUNDLES_CSV,
        SYNC_EVENTS_CSV,
        TRANSFER_PACKAGES_CSV,
    )

    state_root = tmp_path / "mercury_state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / OPERATIONS_JSONL).write_text(
        json.dumps(
            {
                "event_type": "backup_executed",
                "backup_directory_path": "/mnt/MERCURY_DATA_USB/mercury_backups/2026-06-09/db",
            }
        )
        + "\n"
        + json.dumps(
            {
                "event_type": "backup_executed",
                "backup_directory_path": "/tmp/pytest-of-secadmin/pytest-1/backups/db",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (state_root / DATABASE_BACKUPS_CSV).write_text(
        "timestamp,database,role,event,backup_kind,backup_id,backup_path,dump_file,schema_file,size_bytes,verified,restore_check_status,warnings\n"
        "2026-06-09T00:00:00+00:00,erebus_threat_intel_prod,production,verify,full,id1,/mnt/MERCURY_DATA_USB/mercury_backups/2026-06-09/erebus,,,,True,,\n"
        "2026-06-09T00:00:01+00:00,erebus_threat_intel_prod,production,verify,full,id2,/tmp/pytest-of-secadmin/pytest-1/backups/db,,,,True,,\n",
        encoding="utf-8",
    )
    (state_root / REPO_BUNDLES_CSV).write_text(
        "timestamp,repo_name,path,branch,commit,remote,dirty,untracked_count,bundle_path,bundle_verified,bundle_size_bytes,warnings\n"
        "2026-06-09T00:00:00+00:00,Mercury,/repo,main,abc,origin,false,0,/mnt/MERCURY_DATA_USB/mercury_repo_backups/2026-06-09/mercury.bundle,True,1,\n"
        "2026-06-09T00:00:01+00:00,Mercury,/repo,main,abc,origin,false,0,/tmp/pytest-of-secadmin/pytest-1/mercury.bundle,True,1,\n",
        encoding="utf-8",
    )
    (state_root / TRANSFER_PACKAGES_CSV).write_text(
        "timestamp,manifest_path,runbook_path,database_sources,verified_sources,repo_count,dirty_repo_count,sync_ready,sync_blocked,actual_sync_state,warnings\n"
        "2026-06-09T00:00:00+00:00,/mnt/MERCURY_DATA_USB/mercury_manifests/transfer.json,/mnt/MERCURY_DATA_USB/mercury_runbooks/transfer.md,1,1,1,0,1,0,deferred,\n"
        "2026-06-09T00:00:01+00:00,/tmp/pytest-of-secadmin/pytest-1/transfer.json,/tmp/pytest-of-secadmin/pytest-1/transfer.md,1,1,1,0,1,0,deferred,\n",
        encoding="utf-8",
    )
    (state_root / SYNC_EVENTS_CSV).write_text(
        "timestamp,source,target,status,backup_directory,message\n"
        "2026-06-09T00:00:00+00:00,erebus_threat_intel_prod,erebus_threat_intel_dev,executed,/mnt/MERCURY_DATA_USB/mercury_backups/2026-06-09/erebus,ok\n"
        "2026-06-09T00:00:01+00:00,erebus_threat_intel_prod,erebus_threat_intel_dev,executed,/tmp/pytest-of-secadmin/pytest-1/erebus,ok\n",
        encoding="utf-8",
    )

    summary = build_state_summary(state_root=state_root)
    assert summary.operations == 1
    assert summary.database_backup_rows == 1
    assert summary.repo_bundle_rows == 1
    assert summary.transfer_package_rows == 1
    assert summary.sync_event_rows == 1


def test_is_operator_ledger_path_filters_pytest_temp_dirs() -> None:
    from mercury.state.ledger import is_operator_ledger_path, read_operator_database_backup_rows

    assert is_operator_ledger_path("/mnt/MERCURY_DATA_USB/mercury_backups/2026-06-09/db") is True
    assert is_operator_ledger_path("/tmp/pytest-of-secadmin/pytest-1/backups/db") is False


def test_read_operator_database_backup_rows_excludes_pytest_paths(tmp_path: Path) -> None:
    from mercury.state.ledger import DATABASE_BACKUPS_CSV, read_operator_database_backup_rows

    state_root = tmp_path / "ledger_fixture_state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / DATABASE_BACKUPS_CSV).write_text(
        "timestamp,database,role,event,backup_kind,backup_id,backup_path,dump_file,schema_file,size_bytes,verified,restore_check_status,warnings\n"
        "2026-06-09T00:00:00+00:00,erebus_threat_intel_prod,production,verify,full,id1,/mnt/MERCURY_DATA_USB/mercury_backups/2026-06-09/erebus,,,,True,,\n"
        "2026-06-09T00:00:01+00:00,erebus_threat_intel_prod,production,restore_check,,,/tmp/pytest-of-secadmin/pytest-1/test,,,,,failed,msg\n",
        encoding="utf-8",
    )
    rows = read_operator_database_backup_rows(state_root=state_root)
    assert len(rows) == 1
    assert rows[0]["backup_path"].startswith("/mnt/MERCURY_DATA_USB")


def test_resolve_state_root_honors_mercury_state_root_env(tmp_path: Path, monkeypatch) -> None:
    from mercury.state.ledger import ENV_STATE_ROOT, resolve_state_root

    custom = tmp_path / "custom_state"
    monkeypatch.setenv(ENV_STATE_ROOT, str(custom))
    assert resolve_state_root() == custom.resolve()


def test_record_restore_check_verification_failure_uses_failed_status(tmp_path: Path) -> None:
    from mercury.state.ledger import DATABASE_BACKUPS_CSV, OPERATIONS_JSONL, record_restore_check_result

    backup_dir = tmp_path / "backups" / "2026-06-11" / "erebus_threat_intel_prod"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "erebus.sql.gz").write_bytes(b"fake")
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "backup_id": "erebus_threat_intel_prod-full-20260611_120000",
                "source_role": "production_source",
                "backup_kind": "full",
                "dump_file": "erebus.sql.gz",
                "schema_file": "erebus.schema.sql.gz",
                "size_bytes": 123,
                "verified": True,
            }
        ),
        encoding="utf-8",
    )

    result = RestoreExecutionResult(
        source_database="erebus_threat_intel_prod",
        target_database="_restorecheck_erebus_threat_intel_prod_20260611",
        dump_path=str(backup_dir / "erebus.sql.gz"),
        dry_run=False,
        executed=True,
        message="Imported backup, but target verification failed.",
        verification_passed=False,
        verification_issues=["table count is zero"],
    )

    state_root = tmp_path / "state"
    record_restore_check_result(result, state_root=state_root)

    operations = (state_root / OPERATIONS_JSONL).read_text(encoding="utf-8")
    assert "restore_check_verification_failed" in operations

    csv_text = (state_root / DATABASE_BACKUPS_CSV).read_text(encoding="utf-8")
    assert "restore_check,full,erebus_threat_intel_prod-full-20260611_120000" in csv_text
    assert "verification_failed" in csv_text
