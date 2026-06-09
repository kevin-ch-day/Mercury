"""Target-state-aware deployment planning and safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.execution_policy import ExecutionPolicy
from mercury.deploy.actions import resolve_deploy_action
from mercury.deploy.models import DeployOptions
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.runner import execute_deployment_batch
from mercury.deploy.target_status import TargetDatabaseState, classify_target_database

from tests.test_deploy import PROTECTED, _seed_all_verified, _usb_policy


def _classify_map(
    monkeypatch: pytest.MonkeyPatch,
    mapping: dict[str, TargetDatabaseState],
) -> None:
    def fake_classify(database: str, **kwargs) -> TargetDatabaseState:
        return mapping.get(
            database,
            TargetDatabaseState(status="missing", exists_on_server=False, detail="not present"),
        )

    monkeypatch.setattr("mercury.deploy.plan.classify_target_database", fake_classify)


def _mock_server_names(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> None:
    monkeypatch.setattr(
        "mercury.deploy.plan.fetch_user_database_names",
        lambda _cfg: names,
    )
    monkeypatch.setattr(
        "mercury.deploy.preflight.fetch_user_database_names",
        lambda _cfg: names,
    )


def _verified_existing(name: str) -> TargetDatabaseState:
    return TargetDatabaseState(
        status="exists_verified",
        exists_on_server=True,
        detail="appears healthy/verified",
        table_count=3,
        total_bytes=4096,
    )


def test_dry_run_skips_import_when_all_targets_exist_verified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, list(PROTECTED))
    _classify_map(monkeypatch, {name: _verified_existing(name) for name in PROTECTED})

    plan = build_deployment_plan(policy=policy, execute=False)
    assert plan.import_count == 0
    assert plan.skip_count == 3
    assert not plan.planned_commands
    assert not any("gunzip -c" in command for command in plan.planned_commands)
    assert plan.deployment_needed is False
    assert plan.summary_message is not None
    assert "Deployment not needed" in plan.summary_message
    assert all(c.deploy_action == "SKIP" for c in plan.candidates)


def test_dashboard_verified_backups_dry_run_no_import_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """USB backups verified + server DBs present => no import commands in dry-run."""
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, list(PROTECTED))
    _classify_map(monkeypatch, {name: _verified_existing(name) for name in PROTECTED})

    plan = build_deployment_plan(policy=policy, execute=False)
    assert len(plan.candidates) == 3
    assert plan.planned_commands == []
    for candidate in plan.candidates:
        assert candidate.target_status == "exists_verified"
        assert candidate.deploy_action == "SKIP"


def test_skip_existing_default_action_is_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, ["erebus_threat_intel_prod"])
    _classify_map(
        monkeypatch,
        {"erebus_threat_intel_prod": _verified_existing("erebus_threat_intel_prod")},
    )

    plan = build_deployment_plan(
        policy=policy,
        databases=["erebus_threat_intel_prod"],
        options=DeployOptions(skip_existing=True),
        execute=False,
    )
    assert plan.candidates[0].deploy_action == "SKIP"
    assert "skip-existing" in plan.existing_target_policy


def test_existing_target_blocks_live_import_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=_usb_policy(tmp_path).backup_root,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, ["erebus_threat_intel_prod"])
    _classify_map(
        monkeypatch,
        {"erebus_threat_intel_prod": _verified_existing("erebus_threat_intel_prod")},
    )

    plan = build_deployment_plan(
        policy=policy,
        databases=["erebus_threat_intel_prod"],
        options=DeployOptions(skip_existing=True),
        execute=True,
    )
    assert plan.import_count == 0
    assert plan.skip_count == 1
    assert not plan.planned_commands


def test_missing_target_plans_create_and_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, ["mysql", "information_schema"])

    plan = build_deployment_plan(policy=policy, execute=False)
    assert plan.import_count == 3
    assert all(c.deploy_action == "CREATE_AND_IMPORT" for c in plan.candidates)
    assert all(c.target_status == "missing" for c in plan.candidates)
    assert any("gunzip -c" in command for command in plan.planned_commands)
    assert any("CREATE DATABASE IF NOT EXISTS" in command for command in plan.planned_commands)


def test_mixed_missing_and_existing_imports_only_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, list(PROTECTED))
    _classify_map(
        monkeypatch,
        {
            "android_permission_intel": _verified_existing("android_permission_intel"),
            "erebus_threat_intel_prod": _verified_existing("erebus_threat_intel_prod"),
            "scytaledroid_core_prod": TargetDatabaseState(
                status="missing",
                exists_on_server=False,
                detail="not present on server",
            ),
        },
    )

    plan = build_deployment_plan(policy=policy, execute=False)
    assert plan.import_count == 1
    assert plan.skip_count == 2
    import_targets = {c.target_database for c in plan.candidates if c.deploy_action == "CREATE_AND_IMPORT"}
    assert import_targets == {"scytaledroid_core_prod"}


def test_exists_empty_classified_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, ["erebus_threat_intel_prod"])
    _classify_map(
        monkeypatch,
        {
            "erebus_threat_intel_prod": TargetDatabaseState(
                status="exists_empty",
                exists_on_server=True,
                detail="database exists with no tables",
                table_count=0,
            ),
        },
    )

    plan = build_deployment_plan(
        policy=policy,
        databases=["erebus_threat_intel_prod"],
        execute=False,
    )
    assert plan.candidates[0].target_status == "exists_empty"
    assert plan.candidates[0].deploy_action == "SKIP"
    assert not plan.planned_commands


def test_raw_import_only_for_create_and_import_action() -> None:
    action = resolve_deploy_action(
        target_database="erebus_threat_intel_prod",
        dump_path="/tmp/a.sql.gz",
        target_status="missing",
        options=DeployOptions(),
    )
    assert action.action == "CREATE_AND_IMPORT"
    assert any("gunzip -c" in command for command in action.commands)

    skip = resolve_deploy_action(
        target_database="erebus_threat_intel_prod",
        dump_path="/tmp/a.sql.gz",
        target_status="exists_verified",
        options=DeployOptions(skip_existing=True),
    )
    assert skip.action == "SKIP"
    assert skip.commands == []


def test_live_batch_skips_existing_without_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mercury.deploy.models import DeploymentPreflight

    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=_usb_policy(tmp_path).backup_root,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    _seed_all_verified(policy)
    _mock_server_names(monkeypatch, list(PROTECTED))
    _classify_map(monkeypatch, {name: _verified_existing(name) for name in PROTECTED})
    monkeypatch.setattr(
        "mercury.deploy.plan.run_deployment_preflight",
        lambda **kwargs: DeploymentPreflight(
            hostname="test",
            ready=True,
            existing_databases=list(PROTECTED),
        ),
    )

    calls: list[str] = []

    def fake_runner(*args, **kwargs) -> None:
        calls.append("import")

    batch = execute_deployment_batch(
        policy=policy,
        execute=True,
        import_runner=fake_runner,
    )
    assert not calls
    assert batch.skipped_count == 3
    assert batch.deployed_count == 0


def test_plan_shows_policy_labels(tmp_path: Path) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    plan = build_deployment_plan(policy=policy, execute=False)
    assert plan.existing_target_policy == "skip-existing"
    assert plan.overwrite_enabled is False
    assert plan.drop_enabled is False


def test_deployment_snapshot_dashboard_label_when_deploy_not_needed() -> None:
    from mercury.deploy.snapshot import DeploymentSnapshot, deployment_target_dashboard_label

    snapshot = DeploymentSnapshot(
        verified_backup_count=3,
        protected_source_count=3,
        on_server_count=3,
        import_count=0,
        skip_count=3,
        block_count=0,
        deployment_needed=False,
        summary_message="Deployment not needed.",
        candidates=(),
    )
    label = deployment_target_dashboard_label(snapshot)
    assert "3 of 3 on server" in label
    assert "deploy not needed" in label


def test_classify_missing_when_not_on_server() -> None:
    state = classify_target_database(
        "erebus_threat_intel_prod",
        config=None,
        server_databases={"mysql"},
    )
    assert state.status == "missing"
    assert state.exists_on_server is False
