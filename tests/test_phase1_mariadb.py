"""Opt-in real backup/restore round trip on two owned, network-disabled servers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pymysql
import pytest

from mercury.backup.backup_runner import BackupExecutionError, execute_backup
from mercury.backup.checksum import sha256_file
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.deploy.models import DeploymentBatchResult
from mercury.deploy.runner import _execute_client_sql, execute_deployment_for_candidate
from mercury.deploy.selection import resolve_deployment_candidates
from tests.test_phase1_safety import seed_candidate

DB = "erebus_threat_intel_prod"
LITERAL = "DEFINER=`literal`@`data` source.t ☃\nUSE `source`;\nquoted '' content"


@pytest.fixture
def isolated_servers(tmp_path):
    if os.environ.get("MERCURY_PHASE1_INTEGRATION") != "1":
        pytest.skip("MERCURY_PHASE1_INTEGRATION=1 required for owned disposable MariaDB servers")
    required = ("mariadbd", "mariadb-install-db", "mariadb", "mariadb-dump")
    if any(shutil.which(tool) is None for tool in required):
        pytest.skip("Disposable MariaDB tooling unavailable")
    processes = []
    connections = []
    servers = []
    try:
        for name in ("source", "destination"):
            root = tmp_path / name
            root.mkdir(mode=0o700)
            initialized = subprocess.run(["mariadb-install-db", "--no-defaults", f"--datadir={root / 'data'}",
                "--auth-root-authentication-method=normal", "--skip-test-db"], capture_output=True, text=True, timeout=30)
            assert initialized.returncode == 0, initialized.stderr
            socket = root / "server.sock"
            process = subprocess.Popen(["mariadbd", "--no-defaults", f"--datadir={root / 'data'}",
                f"--socket={socket}", f"--pid-file={root / 'server.pid'}", f"--log-error={root / 'server.log'}",
                "--skip-networking", "--port=0", "--innodb-buffer-pool-size=32M"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, (root / "server.log").read_text()
                try:
                    connection = pymysql.connect(unix_socket=str(socket), user="root", autocommit=True, charset="utf8mb4")
                    break
                except pymysql.MySQLError:
                    time.sleep(0.05)
            else:
                pytest.fail("Disposable MariaDB failed to start")
            connections.append(connection)
            with connection.cursor() as cursor:
                cursor.execute("SELECT @@port, @@skip_networking, @@datadir")
                port, skip_networking, datadir = cursor.fetchone()
            assert port == 0 and skip_networking == 1
            assert Path(datadir).resolve() == (root / "data").resolve()
            servers.append((connection, MariaDbConnectionConfig(host="localhost", user="root",
                use_client=True, unix_socket=str(socket))))
        yield servers
    finally:
        for connection in connections:
            connection.close()
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def rows(connection, sql, args=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.fetchall()


def test_protected_backup_with_views_and_unkeyed_rows(isolated_servers, tmp_path, monkeypatch):
    from collections import Counter
    from types import SimpleNamespace

    from mercury.backup import backup_runner, content_contract, protected_generation
    from mercury.backup.checksum import parse_checksum_file
    from mercury.backup.database_fingerprint import fingerprint

    (source, source_cfg), (destination, destination_cfg) = isolated_servers
    rows(source, f"CREATE DATABASE `{DB}` CHARACTER SET utf8mb4")
    rows(source, f"CREATE TABLE `{DB}`.keyed (id INT PRIMARY KEY) ENGINE=InnoDB")
    rows(source, f"INSERT INTO `{DB}`.keyed VALUES (2), (1)")
    rows(source, f"CREATE TABLE `{DB}`.unkeyed (note VARBINARY(8)) ENGINE=InnoDB")
    rows(source, f"INSERT INTO `{DB}`.unkeyed VALUES (X'00'), (NULL), (X'00'), (X'01')")
    rows(source, f"CREATE VIEW `{DB}`.v AS SELECT id FROM `{DB}`.keyed WHERE id > 0")
    expected = fingerprint(source_cfg, DB)
    assert list(expected["views"]) == ["v"]
    assert expected["tables"]["unkeyed"]["rows"] == 4
    assert expected["tables"]["unkeyed"]["primary_key"] == []
    with pytest.raises(ValueError, match="row or byte budget"):
        fingerprint(source_cfg, DB, max_rows=2)
    rows(source, f"CREATE TABLE `{DB}`.unsupported (id INT) ENGINE=MyISAM")
    with pytest.raises(ValueError, match="unsupported.*MyISAM"):
        fingerprint(source_cfg, DB)
    rows(source, f"DROP TABLE `{DB}`.unsupported")

    backup_root = tmp_path / "backups"
    policy = ExecutionPolicy(
        dry_run=False, live_actions_enabled=True, backup_root=backup_root,
        config_path=tmp_path / "synthetic.toml", allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr(ExecutionPolicy, "backup_environment_refusal", lambda self: None)
    monkeypatch.setattr(protected_generation, "load_storage_config", lambda: SimpleNamespace(
        active_write_root=SimpleNamespace(mount_path=tmp_path, filesystem_uuid="synthetic")
    ))
    monkeypatch.setattr(protected_generation, "activate_disk", lambda *_: None)
    monkeypatch.setattr(protected_generation, "require_destination", lambda *_: None)
    monkeypatch.setattr(protected_generation, "require_headroom", lambda *_: None)

    creation_hashes = Counter()

    def counted_hash(path):
        creation_hashes[path.name] += 1
        return sha256_file(path)

    monkeypatch.setattr(backup_runner, "sha256_file", counted_hash)
    monkeypatch.setattr(content_contract, "sha256_file", counted_hash)
    backup = protected_generation.execute_protected_backup(DB, policy=policy, config=source_cfg)
    directory = Path(backup.backup_directory_path)
    manifest = backup.manifest
    assert manifest.verified
    assert creation_hashes == {manifest.dump_file: 1, manifest.schema_file: 1}
    sidecar = parse_checksum_file(directory / "checksum.sha256")
    assert manifest.sha256 == sidecar[manifest.dump_file] == manifest.restore_requirements["artifact_sha256"]
    assert manifest.schema_sha256 == sidecar[manifest.schema_file]
    assert json.loads((directory / "SOURCE_FINGERPRINT.json").read_text()) == expected
    assert verify_backup_artifacts(directory, database=DB, require_complete_full=True).verified

    candidate, = resolve_deployment_candidates(policy=policy, databases=[DB])
    result = execute_deployment_for_candidate(candidate=candidate, execute=True, policy=policy, config=destination_cfg)
    assert result.succeeded, result.model_dump()
    assert fingerprint(destination_cfg, DB) == expected

    rows(destination, f"DELETE FROM `{DB}`.unkeyed")
    rows(destination, f"INSERT INTO `{DB}`.unkeyed VALUES (X'01'), (X'00'), (X'00'), (NULL)")
    assert fingerprint(destination_cfg, DB) == expected
    rows(destination, f"UPDATE `{DB}`.unkeyed SET note = X'01' WHERE note = X'00' LIMIT 1")
    changed = fingerprint(destination_cfg, DB)
    assert changed["tables"]["unkeyed"]["rows"] == expected["tables"]["unkeyed"]["rows"]
    assert changed["tables"]["unkeyed"]["typed_rows_sha256"] != expected["tables"]["unkeyed"]["typed_rows_sha256"]
    rows(destination, f"ALTER VIEW `{DB}`.v AS SELECT id FROM `{DB}`.keyed WHERE id > 1")
    assert fingerprint(destination_cfg, DB)["views"] != expected["views"]


def test_real_phase1_backup_recovery_and_failures(isolated_servers, tmp_path):
    (source, source_cfg), (destination, destination_cfg) = isolated_servers
    rows(source, f"CREATE DATABASE `{DB}` CHARACTER SET utf8mb4")
    rows(source, f"CREATE TABLE `{DB}`.t (id INT PRIMARY KEY, note TEXT) ENGINE=InnoDB")
    rows(source, f"INSERT INTO `{DB}`.t VALUES (1, %s)", (LITERAL,))
    rows(source, f"CREATE VIEW `{DB}`.v AS SELECT id,note FROM `{DB}`.t")
    quoted = LITERAL.replace("'", "''")
    rows(source, f"CREATE PROCEDURE `{DB}`.p() SQL SECURITY DEFINER SELECT '{quoted}' AS note")
    rows(source, f"CREATE FUNCTION `{DB}`.f() RETURNS TEXT DETERMINISTIC RETURN '{quoted}'")
    rows(source, f"CREATE TRIGGER `{DB}`.trg BEFORE INSERT ON `{DB}`.t FOR EACH ROW SET NEW.note = CONCAT(NEW.note, '|DEFINER=`trigger`@`data`')")
    rows(source, f"CREATE EVENT `{DB}`.e ON SCHEDULE EVERY 1 DAY DISABLE DO SET @audit = 1")
    before = rows(source, f"SELECT * FROM `{DB}`.t ORDER BY id")

    policy = ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=tmp_path / "backups",
        config_path=tmp_path / "synthetic.toml", allow_unsafe_backup_root=True)
    instant = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    backup = execute_backup(DB, "full", execute=True, policy=policy, now=instant, mariadb_config=source_cfg,
        live=True, server_names={DB}, record_execution=False)
    assert backup.executed and not backup.refused
    directory = Path(backup.backup_directory_path)
    assert verify_backup_artifacts(directory, database=DB).verified
    pinned_bytes = {p.name: sha256_file(p) for p in directory.iterdir()}
    with pytest.raises(BackupExecutionError, match="identity already exists"):
        execute_backup(DB, "full", execute=True, policy=policy, now=instant, mariadb_config=source_cfg,
            live=True, server_names={DB}, record_execution=False)
    assert {p.name: sha256_file(p) for p in directory.iterdir()} == pinned_bytes
    assert rows(source, f"SELECT * FROM `{DB}`.t ORDER BY id") == before

    candidate, = resolve_deployment_candidates(policy=policy, databases=[DB])
    result = execute_deployment_for_candidate(candidate=candidate, execute=True, policy=policy, config=destination_cfg)
    assert result.succeeded, result.model_dump()
    assert result.verification.detail.startswith("manifest object contract verified")
    assert rows(destination, f"SELECT * FROM `{DB}`.t ORDER BY id") == before
    assert rows(destination, f"SELECT * FROM `{DB}`.v ORDER BY id") == before
    assert rows(destination, f"CALL `{DB}`.p()") == ((LITERAL,),)
    assert rows(destination, f"SELECT `{DB}`.f()") == ((LITERAL,),)
    rows(destination, f"INSERT INTO `{DB}`.t VALUES (2, 'new')")
    assert rows(destination, f"SELECT note FROM `{DB}`.t WHERE id=2") == (("new|DEFINER=`trigger`@`data`",),)
    assert rows(destination, "SELECT SECURITY_TYPE FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=%s AND ROUTINE_NAME='p'", (DB,)) == (("DEFINER",),)
    assert rows(destination, "SELECT STATUS FROM information_schema.EVENTS WHERE EVENT_SCHEMA=%s AND EVENT_NAME='e'", (DB,)) == (("DISABLED",),)

    # A real concurrent creator wins before Mercury's CREATE. The sentinel
    # must survive even though the dump contains destructive table statements.
    raced = seed_candidate(policy, database="scytaledroid_core_prod", sql=b"DROP TABLE IF EXISTS sentinel;\nCREATE TABLE t(id INT);\n")
    def concurrent_create(cfg, sql):
        rows(destination, "CREATE DATABASE scytaledroid_core_prod")
        rows(destination, "CREATE TABLE scytaledroid_core_prod.sentinel(id INT)")
        rows(destination, "INSERT INTO scytaledroid_core_prod.sentinel VALUES (99)")
        _execute_client_sql(cfg, sql)
    race_result = execute_deployment_for_candidate(candidate=raced, execute=True, policy=policy,
        config=destination_cfg, sql_runner=concurrent_create)
    assert race_result.refused and not race_result.target_created and not race_result.executed
    assert rows(destination, "SELECT * FROM scytaledroid_core_prod.sentinel") == ((99,),)
    assert rows(destination, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='scytaledroid_core_prod'") == ((1,),)

    failed = seed_candidate(policy, database="obsidiandroid_core_prod")
    manifest_path = Path(failed.manifest_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["object_contract"] = {"dump": {"tables": ["t"], "triggers": ["missing_required_trigger"]}}
    manifest_path.write_text(json.dumps(manifest))
    from mercury.deploy.artifacts import pin_deployment_artifacts
    failed = failed.model_copy(update=pin_deployment_artifacts(manifest_path.parent,
        database=failed.source_database, backup_id=failed.backup_id))
    failed_result = execute_deployment_for_candidate(candidate=failed, execute=True, policy=policy, config=destination_cfg)
    batch = DeploymentBatchResult(hostname="disposable", results=[failed_result])
    assert failed_result.executed and failed_result.refused and failed_result.target_created
    assert not failed_result.verification.verified
    assert batch.deployed_count == 0 and batch.failed_count == 1
    assert "missing_required_trigger" in " ".join(failed_result.verification.issues)
    assert rows(destination, "SELECT * FROM obsidiandroid_core_prod.t") == ((7,),)

    corrupted = seed_candidate(policy, database="android_permission_intel")
    Path(corrupted.dump_path).write_bytes(b"substituted after selection")
    corrupt_result = execute_deployment_for_candidate(candidate=corrupted, execute=True, policy=policy, config=destination_cfg)
    assert corrupt_result.refused and not corrupt_result.target_created
    assert rows(destination, "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='android_permission_intel'") == ((0,),)

    # Intentionally invalid SQL can affect only Mercury's newly created schema.
    invalid_root = tmp_path / "invalid-input"
    invalid_policy = ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=invalid_root,
        config_path=policy.config_path, allow_unsafe_backup_root=True)
    invalid = seed_candidate(invalid_policy, database="android_permission_intel", sql=b"THIS IS NOT VALID SQL;\n")
    invalid_result = execute_deployment_for_candidate(candidate=invalid, execute=True, policy=invalid_policy, config=destination_cfg)
    assert invalid_result.refused and invalid_result.target_created and not invalid_result.executed
    assert "retained for inspection" in invalid_result.message

    noctivar = seed_candidate(policy, database="noctivar")
    with pytest.raises(BackupExecutionError, match="no deployment authorization"):
        execute_deployment_for_candidate(candidate=noctivar, execute=True, policy=policy, config=destination_cfg)
    assert rows(destination, "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='noctivar'") == ((0,),)
    assert rows(source, f"SELECT * FROM `{DB}`.t ORDER BY id") == before
