"""Phase 1 fault injection: owned files and mocked SQL only; never live DDL."""

from __future__ import annotations

import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from typer.testing import CliRunner

from mercury.backup.backup_runner import BackupExecutionError, execute_backup
from mercury.backup.checksum import sha256_file, write_checksum_file
from mercury.core.execution_policy import ExecutionPolicy
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.deploy.artifacts import pin_deployment_artifacts
from mercury.deploy.models import (
    DeploymentBatchResult, DeploymentCandidate, DeploymentPlan, DeploymentVerification, DeployOptions,
)
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.runner import execute_deployment_batch, execute_deployment_for_candidate
from mercury.deploy.safety import assert_deployment_target
from mercury.deploy.selection import resolve_deployment_candidates

DB = "erebus_threat_intel_prod"
SQL = b"CREATE TABLE t(id INT);\nINSERT INTO t VALUES (7);\n"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def safety_policy(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    return ExecutionPolicy(dry_run=False, live_actions_enabled=True, backup_root=root,
        config_path=tmp_path / "local.toml", allow_unsafe_backup_root=True)


@pytest.fixture
def fake_config():
    return MariaDbConnectionConfig(host="localhost", user="synthetic", use_client=True,
        unix_socket="/nonexistent/mercury-unit-test.sock")


def seed_candidate(policy, *, database=DB, sql=SQL):
    directory = policy.backup_root / "2026-11-08" / database / "20261108_120000_000"
    directory.mkdir(parents=True)
    dump = directory / "data.sql.gz"
    dump.write_bytes(gzip.compress(sql, mtime=0))
    backup_id = f"{database}-full-20261108_120000_000"
    manifest = dict(database=database, backup_id=backup_id, backup_kind="full", created_at=NOW.isoformat(),
        dump_file=dump.name, sha256=sha256_file(dump), size_bytes=dump.stat().st_size,
        source_role="production", verified=True, live_actions_enabled=True, dry_run=False)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    write_checksum_file(directory, [dump.name])
    pins = pin_deployment_artifacts(directory, database=database, backup_id=backup_id,
        allow_development_backup=database.endswith("_dev"))
    return DeploymentCandidate(source_database=database, target_database=database,
        backup_directory=str(directory), backup_id=backup_id, dump_path=str(dump),
        manifest_path=str(directory / "manifest.json"), checksum_path=str(directory / "checksum.sha256"), **pins)


def mocked_deploy(candidate, policy, config, *, sql_runner=None, import_runner=None):
    return execute_deployment_for_candidate(candidate=candidate, execute=True, policy=policy,
        config=config, sql_runner=sql_runner or (lambda *_: None),
        import_runner=import_runner or (lambda *_: None), inspect_row_fn=lambda *_: (1, 1, 0, 16384))


def fake_dump(argv, env, output, config):
    assert not (output.parent / "manifest.json").exists()
    output.write_bytes(gzip.compress(SQL, mtime=0))


def run_backup(policy, config, *, runner=fake_dump, **kwargs):
    return execute_backup(DB, "full", execute=True, policy=policy, now=NOW, mariadb_config=config,
        dump_runner=runner, record_execution=False, **kwargs)


def test_repeated_backup_identity_never_touches_existing_bytes(safety_policy, fake_config):
    first = run_backup(safety_policy, fake_config)
    directory = Path(first.backup_directory_path)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    def unexpected_dump(*_):
        pytest.fail("An existing identity must be refused before dumping or cleanup")
    with pytest.raises(BackupExecutionError, match="identity already exists"):
        run_backup(safety_policy, fake_config, runner=unexpected_dump)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_concurrent_same_timestamp_has_exactly_one_owner(safety_policy, fake_config):
    barrier = Barrier(2)
    def contender():
        barrier.wait(timeout=5)
        try:
            return run_backup(safety_policy, fake_config).executed
        except BackupExecutionError as exc:
            assert "identity already exists" in str(exc)
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: contender(), range(2)))
    assert sorted(results) == [False, True]
    from mercury.backup.verification import verify_backup_artifacts
    directories = list(safety_policy.backup_root.glob("*/*/*"))
    assert len(directories) == 1
    assert verify_backup_artifacts(directories[0]).verified


def test_failed_backup_cleanup_never_globs_unowned_files(safety_policy, fake_config):
    def fail_companion(argv, env, output, config):
        if "--no-data" in argv:
            (output.parent / "unowned.tmp").write_bytes(b"preserve me")
            output.write_bytes(b"partial owned output")
            raise BackupExecutionError("synthetic schema dump failure")
        fake_dump(argv, env, output, config)
    with pytest.raises(BackupExecutionError, match="synthetic schema"):
        run_backup(safety_policy, fake_config, runner=fail_companion)
    directory = next(safety_policy.backup_root.glob("*/*/*"))
    assert [p.name for p in directory.iterdir()] == ["unowned.tmp"]
    assert (directory / "unowned.tmp").read_bytes() == b"preserve me"
    with pytest.raises(BackupExecutionError, match="identity already exists"):
        run_backup(safety_policy, fake_config)


@pytest.mark.parametrize("populated", [False, True])
def test_protected_publication_cannot_replace_reserved_generation(tmp_path, populated):
    from mercury.backup.protected_generation import _publish_generation

    staged, final = tmp_path / "staged", tmp_path / "final"
    staged.mkdir()
    final.mkdir()
    (staged / "manifest.json").write_bytes(b"new manifest")
    if populated:
        (final / "prior.sql.gz").write_bytes(b"prior backup")
    with pytest.raises(BackupExecutionError, match="overwrite"):
        _publish_generation(staged, final, database=DB, require_complete_full=True, guard=lambda _: None)
    assert (staged / "manifest.json").read_bytes() == b"new manifest"
    assert not (final / "manifest.json").exists()
    if populated:
        assert (final / "prior.sql.gz").read_bytes() == b"prior backup"


@pytest.mark.parametrize("timestamp", ["../escape", "/absolute", "a\\b", ".."])
def test_backup_identity_cannot_escape_reservation(safety_policy, fake_config, timestamp):
    with pytest.raises(BackupExecutionError, match="safe directory names"):
        run_backup(safety_policy, fake_config, timestamp=timestamp)
    assert not list(safety_policy.backup_root.iterdir())


def test_existing_target_race_refuses_before_import(safety_policy, fake_config):
    candidate = seed_candidate(safety_policy)
    imports = []
    def raced_create(_config, sql):
        assert sql == f"CREATE DATABASE `{DB}`"
        raise BackupExecutionError("database exists (synthetic concurrent creator)")
    result = mocked_deploy(candidate, safety_policy, fake_config, sql_runner=raced_create,
        import_runner=lambda *_: imports.append(True))
    assert result.refused and not result.executed and not result.target_created
    assert imports == []


@pytest.mark.parametrize("field", ["dump_path", "manifest_path", "checksum_path", "backup_id",
    "manifest_sha256", "checksum_sha256", "dump_sha256"])
def test_candidate_substitution_refused_before_target_ddl(safety_policy, fake_config, tmp_path, field):
    candidate = seed_candidate(safety_policy)
    if field.endswith("_path"):
        outside = tmp_path / field
        original = Path(getattr(candidate, field))
        outside.write_bytes(original.read_bytes())
        setattr(candidate, field, str(outside))
    else:
        setattr(candidate, field, "0" * 64)
    calls = []
    result = mocked_deploy(candidate, safety_policy, fake_config,
        sql_runner=lambda *_: calls.append("DDL"), import_runner=lambda *_: calls.append("import"))
    assert result.refused and not result.executed
    assert calls == []


@pytest.mark.parametrize("name", ["data.sql.gz", "manifest.json", "checksum.sha256"])
def test_source_changes_after_selection_are_refused(safety_policy, fake_config, name):
    candidate = seed_candidate(safety_policy)
    path = Path(candidate.backup_directory) / name
    path.write_bytes(path.read_bytes() + b"\nchanged")
    calls = []
    result = mocked_deploy(candidate, safety_policy, fake_config, sql_runner=lambda *_: calls.append(True))
    assert result.refused and not result.target_created and not calls


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO refusal is a POSIX test")
@pytest.mark.parametrize("field", ["dump_path", "manifest_path", "checksum_path"])
def test_special_file_substitution_is_refused_without_waiting(safety_policy, fake_config, field):
    candidate = seed_candidate(safety_policy)
    path = Path(getattr(candidate, field))
    path.unlink()
    os.mkfifo(path)
    result = mocked_deploy(candidate, safety_policy, fake_config,
        sql_runner=lambda *_: pytest.fail("Special files cannot authorize DDL"))
    assert result.refused and not result.target_created


@pytest.mark.parametrize("kind", ["dump", "manifest", "checksum", "ancestor"])
def test_symlink_redirection_refused(safety_policy, fake_config, tmp_path, kind):
    candidate = seed_candidate(safety_policy)
    if kind == "ancestor":
        directory = Path(candidate.backup_directory)
        moved = directory.with_name("moved")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    else:
        path = Path(getattr(candidate, {"dump": "dump_path", "manifest": "manifest_path", "checksum": "checksum_path"}[kind]))
        other = tmp_path / path.name
        path.rename(other)
        path.symlink_to(other)
    calls = []
    result = mocked_deploy(candidate, safety_policy, fake_config, sql_runner=lambda *_: calls.append(True))
    assert result.refused and calls == []


def test_import_and_verification_use_private_pinned_copy(safety_policy, fake_config, monkeypatch):
    candidate = seed_candidate(safety_policy)
    original_dump = Path(candidate.dump_path).read_bytes()
    original_manifest = Path(candidate.manifest_path).read_bytes()
    captures = []
    def concurrent_source_change(_config, sql):
        assert sql == f"CREATE DATABASE `{DB}`"
        Path(candidate.dump_path).write_bytes(b"changed after verification")
        Path(candidate.manifest_path).write_text("{}")
    def capture_import(argv, env, path, config, target):
        assert path != Path(candidate.dump_path)
        captures.append(path.read_bytes())
    def capture_verification(database, *, manifest_path, **kwargs):
        assert manifest_path != Path(candidate.manifest_path)
        assert manifest_path.read_bytes() == original_manifest
        return DeploymentVerification(database=database, verified=True, exists_on_server=True)
    monkeypatch.setattr("mercury.deploy.runner.verify_deployed_database", capture_verification)
    result = mocked_deploy(candidate, safety_policy, fake_config,
        sql_runner=concurrent_source_change, import_runner=capture_import)
    assert result.succeeded and captures == [original_dump]
    assert result.artifact_binding["dump_sha256"] == candidate.dump_sha256


def test_callback_cannot_substitute_validated_candidate_target(safety_policy, fake_config):
    candidate = seed_candidate(safety_policy)
    targets = []
    def mutate_candidate(_config, sql):
        assert sql == f"CREATE DATABASE `{DB}`"
        candidate.target_database = "noctivar"
    result = mocked_deploy(candidate, safety_policy, fake_config, sql_runner=mutate_candidate,
        import_runner=lambda argv, env, path, config, target: targets.append(target))
    assert result.succeeded and targets == [DB]


def test_failed_post_verification_stops_batch_and_writes_failure_evidence(safety_policy, fake_config, monkeypatch):
    candidate = seed_candidate(safety_policy)
    untouched = seed_candidate(safety_policy, database="scytaledroid_core_prod")
    monkeypatch.setattr("mercury.deploy.runner.build_deployment_plan", lambda **_: DeploymentPlan(
        mode="live", hostname="synthetic", mariadb_user="synthetic", candidates=[candidate, untouched]))
    monkeypatch.setattr("mercury.deploy.runner.try_load_mariadb_config", lambda: fake_config)
    monkeypatch.setattr("mercury.deploy.runner.verify_deployed_database", lambda database, **_: DeploymentVerification(
        database=database, exists_on_server=True, verified=False, issues=["missing required trigger"]))
    ddl, imports = [], []
    batch = execute_deployment_batch(policy=safety_policy, execute=True,
        sql_runner=lambda _, sql: ddl.append(sql), import_runner=lambda *_: imports.append(True))
    assert batch.deployed_count == 0 and batch.failed_count == 1
    assert ddl == [f"CREATE DATABASE `{DB}`"] and imports == [True]
    assert len(batch.results) == 1 and batch.results[0].target_created
    receipt = json.loads(Path(batch.report_path).read_text())
    assert receipt["results"][0]["verification"]["issues"] == ["missing required trigger"]
    assert receipt["results"][0]["refused"]
    assert "verification failed" in batch.results[0].message


def test_unavailable_verification_cannot_report_success(safety_policy, fake_config, monkeypatch):
    candidate = seed_candidate(safety_policy)
    def unavailable(*_, **__):
        raise OSError("synthetic inspection disconnected")
    monkeypatch.setattr("mercury.deploy.runner.verify_deployed_database", unavailable)
    result = mocked_deploy(candidate, safety_policy, fake_config)
    batch = DeploymentBatchResult(hostname="synthetic", results=[result])
    assert result.executed and result.refused and not result.succeeded
    assert batch.deployed_count == 0 and batch.failed_count == 1
    assert "disconnected" in result.verification.issues[0]


@pytest.mark.parametrize("contract", [{"source": {}}, {"dump": "invalid"}, {"dump": {"tables": 4}}])
def test_malformed_required_contract_never_falls_back_to_basic_success(tmp_path, fake_config, contract):
    from mercury.deploy.verification import verify_deployed_database

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"object_contract": contract}))
    result = verify_deployed_database(DB, manifest_path=manifest, config=fake_config,
        row_fn=lambda *_: (1, 1, 0, 16384))
    assert not result.verified and result.issues


def test_cli_deployment_returns_failure_for_unverified_import(monkeypatch):
    from mercury.cli import app
    from mercury.deploy.models import DeploymentExecutionResult
    result = DeploymentExecutionResult(source_database=DB, target_database=DB, executed=True,
        verification=DeploymentVerification(database=DB, issues=["not verified"]))
    batch = DeploymentBatchResult(hostname="synthetic", results=[result])
    monkeypatch.setattr("mercury.deploy.runner.execute_deployment_batch", lambda **_: batch)
    response = CliRunner().invoke(app, ["deploy", "db", "--execute", "--confirm", "DEPLOY VERIFIED BACKUPS"])
    assert response.exit_code == 1, response.output
    assert "Failed" in response.output and "not verified" in response.output


@pytest.mark.parametrize("flag", ["--execute", "--no-dry-run"])
def test_live_deployment_requires_separate_confirmation(monkeypatch, flag):
    from mercury.cli import app

    monkeypatch.setattr("mercury.deploy.runner.execute_deployment_batch", lambda **_: pytest.fail("Unconfirmed deployment"))
    response = CliRunner().invoke(app, ["deploy", "db", flag])
    assert response.exit_code != 0
    assert "DEPLOY VERIFIED BACKUPS" in response.output


def test_approved_plan_is_not_reselected_after_confirmation(safety_policy, fake_config, monkeypatch):
    candidate = seed_candidate(safety_policy)
    plan = DeploymentPlan(hostname="synthetic", mariadb_user="synthetic", candidates=[candidate])
    monkeypatch.setattr("mercury.deploy.runner.build_deployment_plan", lambda **_: pytest.fail("Approved inputs must not be reselected"))
    monkeypatch.setattr("mercury.deploy.runner.try_load_mariadb_config", lambda: fake_config)
    result = execute_deployment_batch(policy=safety_policy, execute=True, deployment_plan=plan,
        sql_runner=lambda *_: None, import_runner=lambda *_: None, inspect_row_fn=lambda *_: (1, 1, 0, 16384))
    assert result.deployed_count == 1 and result.failed_count == 0
    assert result.mode == "live" and Path(result.report_path).is_file()
    assert plan.mode == "dry-run"  # Approval input is preserved, not mutated.


@pytest.mark.parametrize("allow_dev", [False, True])
@pytest.mark.parametrize("database", ["noctivar", "erebus_provider_secrets_prod", "unreviewed_prod"])
def test_backup_eligibility_never_confers_deployment_authority(database, allow_dev):
    from mercury.database.core import classify_database
    assert classify_database(database).backup_source
    with pytest.raises(BackupExecutionError, match="no deployment authorization"):
        assert_deployment_target(database, allow_development_deploy=allow_dev)


def test_noctivar_is_blocked_in_selection_plan_and_executor(safety_policy, fake_config, monkeypatch):
    candidate = seed_candidate(safety_policy, database="noctivar")
    assert resolve_deployment_candidates(policy=safety_policy, databases=["noctivar"]) == []
    from mercury.deploy.models import DeploymentPreflight
    monkeypatch.setattr("mercury.deploy.plan.run_deployment_preflight", lambda **_: DeploymentPreflight(hostname="test"))
    monkeypatch.setattr("mercury.deploy.plan.try_load_mariadb_config", lambda: None)
    plan = build_deployment_plan(policy=safety_policy, databases=["noctivar"], execute=True)
    assert not plan.allowed and any("no deployment authorization" in s for s in plan.blockers)
    with pytest.raises(BackupExecutionError, match="no deployment authorization"):
        mocked_deploy(candidate, safety_policy, fake_config)


@pytest.mark.parametrize("override", [None, "governed_destination_rehearsal", "governed_production_cutover", "governed_destination_recovery"])
def test_noctivar_source_cannot_bypass_policy_via_live_restore(override, safety_policy, fake_config, tmp_path):
    from mercury.restore.restore_runner import execute_restore_into_database

    kwargs = {override: True} if override else {}
    with pytest.raises(BackupExecutionError, match="backup-only; live restore is prohibited"):
        execute_restore_into_database(source_database="noctivar", target_database="_restorecheck_noctivar",
            dump_path=tmp_path / "nonexistent.sql.gz", execute=True, policy=safety_policy,
            config=fake_config, **kwargs)


def test_source_target_substitution_and_unpinned_candidates_are_refused(safety_policy, fake_config):
    candidate = seed_candidate(safety_policy)
    with pytest.raises(BackupExecutionError, match="exact authorized source/target"):
        mocked_deploy(candidate.model_copy(update={"target_database": "scytaledroid_core_prod"}), safety_policy, fake_config)
    unpinned = candidate.model_copy(update={"manifest_sha256": None})
    result = mocked_deploy(unpinned, safety_policy, fake_config,
        sql_runner=lambda *_: pytest.fail("Unpinned input cannot authorize DDL"))
    assert result.refused and not result.target_created


def test_overwrite_options_remain_disabled_even_for_missing_target(safety_policy, fake_config):
    candidate = seed_candidate(safety_policy)
    result = execute_deployment_for_candidate(candidate=candidate, execute=True, policy=safety_policy,
        config=fake_config, options=DeployOptions(allow_overwrite_database=True, allow_drop_database=True),
        sql_runner=lambda *_: pytest.fail("No SQL permitted"))
    assert result.refused and not result.commands


def test_unsupported_sql_transformation_refused_before_ddl(safety_policy, fake_config):
    candidate = seed_candidate(safety_policy, sql=b"SET SQL_MODE='NO_BACKSLASH_ESCAPES';\n" + SQL)
    result = mocked_deploy(candidate, safety_policy, fake_config,
        sql_runner=lambda *_: pytest.fail("Unsupported transforms must fail before target creation"))
    assert result.refused and not result.target_created
    assert "unsupported quote semantics" in result.message
