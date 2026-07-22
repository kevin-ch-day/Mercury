"""Tests for database deployment from verified operator-storage backups."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.backup.checksum import write_checksum_file
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.deploy.models import DeployOptions
from mercury.deploy.plan import build_deployment_plan
from mercury.deploy.preflight import run_deployment_preflight
from mercury.deploy.runner import build_import_shell_preview, execute_deployment_batch
from mercury.deploy.safety import planned_import_commands
from mercury.deploy.selection import resolve_deployment_candidates
from mercury.deploy.privileges import deployment_grant_repair_sql, deployment_grants_sufficient
from mercury.deploy.verification import verify_deployed_database
from mercury.menu import main_display as menu_display
from mercury.menu.actions import menu_actions
from mercury.env.doctor import build_repair_plan, DoctorReport

from mercury.core.paths import REPO_ROOT


PROTECTED = (
    "erebus_threat_intel_prod",
    "scytaledroid_core_prod",
    "android_permission_intel",
    "obsidiandroid_core_prod",
)


def _write_verified_backup(
    backup_root: Path,
    database: str,
    *,
    backup_id: str,
    created_at: str,
    dump_bytes: bytes = b"CREATE TABLE t (id INT);\nINSERT INTO t VALUES (1);\n",
) -> Path:
    day = created_at[:10]
    backup_dir = backup_root / day / database
    backup_dir.mkdir(parents=True, exist_ok=True)
    dump_name = f"{database}_{backup_id}.sql.gz"
    dump_path = backup_dir / dump_name
    dump_path.write_bytes(dump_bytes)
    manifest = {
        "backup_id": backup_id,
        "database": database,
        "backup_kind": BACKUP_KIND_FULL,
        "created_at": created_at,
        "dump_file": dump_name,
        "schema_file": None,
        "sha256": "placeholder",
        "size_bytes": len(dump_bytes),
        "source_role": "production",
        "tool_used": "mariadb-dump",
        "verified": True,
        "live_actions_enabled": True,
        "dry_run": False,
        "notes": "",
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_checksum_file(backup_dir, [dump_name])
    return backup_dir


def _usb_policy(tmp_path: Path, *, config_path: Path | None = None) -> ExecutionPolicy:
    usb = tmp_path / "usb"
    backup_root = usb / "mercury_backups"
    backup_root.mkdir(parents=True)
    local = config_path or tmp_path / "local.toml"
    if not local.exists():
        local.write_text("[mercury]\n", encoding="utf-8")
    return ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=backup_root,
        config_path=local,
        allow_unsafe_backup_root=True,
    )


def _seed_all_verified(policy: ExecutionPolicy) -> None:
    stamp = "2026-06-09T12:00:00+00:00"
    for index, name in enumerate(PROTECTED):
        _write_verified_backup(
            policy.backup_root,
            name,
            backup_id=f"b{index}",
            created_at=stamp,
        )


def test_deploy_lane_appears_in_menu() -> None:
    keys = {item.key for _section, items in menu_display.MENU_SECTIONS for item in items}
    assert "8" in keys
    assert menu_actions()["8"].title == "System Deployment"


def test_database_deploy_status_rows_describe_missing_databases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from mercury.deploy.menu_status import database_deploy_status_rows
    from mercury.deploy.snapshot import DeploymentSnapshot

    policy = _usb_policy(tmp_path)
    snapshot = DeploymentSnapshot(
        verified_backup_count=1,
        protected_source_count=1,
        on_server_count=0,
        missing_source_count=1,
        import_count=1,
        skip_count=0,
        block_count=0,
        deployment_needed=True,
        summary_message=None,
        candidates=(),
    )
    monkeypatch.setattr("mercury.deploy.menu_status.load_execution_policy", lambda: policy)
    monkeypatch.setattr("mercury.deploy.menu_status.build_deployment_snapshot", lambda **kwargs: snapshot)
    text = "\n".join(database_deploy_status_rows())
    assert "0 of 1 protected sources on server" in text
    assert "1 missing" in text
    assert "DRY RUN" in text
    assert "Option 2" in text


def test_deploy_preflight_blocked_when_config_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "backups",
        config_path=None,
        allow_unsafe_backup_root=True,
    )
    monkeypatch.setattr(
        "mercury.deploy.preflight.build_environment_status",
        lambda **kwargs: SimpleNamespace(
            config=SimpleNamespace(initialized=False, local_toml_present=False),
            mariadb=SimpleNamespace(
                service_state="active",
                mariadb_client_found=True,
                connection_works=None,
                configured_user="linuxadmin",
            ),
        ),
    )
    preflight = run_deployment_preflight(policy=policy, probe_database=False)
    assert not preflight.ready
    assert any("config init" in detail.lower() for detail in preflight.blockers)


def test_deploy_preflight_blocked_when_usb_backup_root_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "repo" / "backups",
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=False,
    )
    (policy.config_path).write_text("[mercury]\n", encoding="utf-8")
    monkeypatch.setattr(
        "mercury.deploy.preflight.build_environment_status",
        lambda **kwargs: SimpleNamespace(
            config=SimpleNamespace(initialized=True, local_toml_present=True),
            mariadb=SimpleNamespace(
                service_state="active",
                mariadb_client_found=True,
                connection_works=True,
                configured_user="linuxadmin",
            ),
        ),
    )
    monkeypatch.setattr("mercury.deploy.preflight.try_load_mariadb_config", lambda: None)
    monkeypatch.setattr("mercury.deploy.preflight.fetch_user_database_names", lambda _cfg: [])
    preflight = run_deployment_preflight(policy=policy, probe_database=False)
    assert not preflight.ready
    assert any(
        "repo-local" in detail.lower()
        or "usb" in detail.lower()
        or "operator backup root" in detail.lower()
        for detail in preflight.blockers
    )


def test_deploy_preflight_blocked_when_mariadb_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    monkeypatch.setattr(
        "mercury.deploy.preflight.build_environment_status",
        lambda **kwargs: SimpleNamespace(
            config=SimpleNamespace(initialized=True, local_toml_present=True),
            mariadb=SimpleNamespace(
                service_state="active",
                mariadb_client_found=True,
                connection_works=False,
                configured_user="linuxadmin",
            ),
        ),
    )
    monkeypatch.setattr("mercury.deploy.preflight.try_load_mariadb_config", lambda: None)
    preflight = run_deployment_preflight(policy=policy, probe_database=True)
    assert not preflight.ready
    assert any("auth failed" in detail.lower() or "connection" in detail.lower() for detail in preflight.blockers)


def test_deploy_plan_from_verified_usb_backups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    monkeypatch.setattr("mercury.deploy.plan.fetch_user_database_names", lambda _cfg: [])
    monkeypatch.setattr("mercury.deploy.preflight.fetch_user_database_names", lambda _cfg: [])
    plan = build_deployment_plan(policy=policy, execute=False)
    assert len(plan.candidates) == 4
    assert plan.mode == "dry-run"
    assert plan.planned_commands


def test_latest_verified_backup_set_chooses_newest_per_db(tmp_path: Path) -> None:
    policy = _usb_policy(tmp_path)
    _write_verified_backup(
        policy.backup_root,
        "erebus_threat_intel_prod",
        backup_id="old",
        created_at="2026-06-08T10:00:00+00:00",
    )
    newer = _write_verified_backup(
        policy.backup_root,
        "erebus_threat_intel_prod",
        backup_id="new",
        created_at="2026-06-09T18:00:00+00:00",
    )
    candidates = resolve_deployment_candidates(policy=policy, databases=["erebus_threat_intel_prod"])
    assert len(candidates) == 1
    assert candidates[0].backup_id == "new"
    assert candidates[0].backup_directory == str(newer)


def test_missing_source_dbs_do_not_block_deploy_planning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    monkeypatch.setattr(
        "mercury.deploy.plan.fetch_user_database_names",
        lambda _cfg: ["mysql", "information_schema"],
    )
    plan = build_deployment_plan(policy=policy, execute=False)
    assert len(plan.candidates) == 4
    assert not any("missing on server" in blocker.lower() for blocker in plan.blockers)


def test_existing_target_db_blocks_live_deploy_by_default(
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
    monkeypatch.setattr(
        "mercury.deploy.plan.fetch_user_database_names",
        lambda _cfg: list(PROTECTED),
    )
    monkeypatch.setattr(
        "mercury.deploy.plan.classify_target_database",
        lambda database, **kwargs: __import__(
            "mercury.deploy.target_status", fromlist=["TargetDatabaseState"]
        ).TargetDatabaseState(
            status="exists_verified",
            exists_on_server=True,
            detail="appears healthy/verified",
            table_count=1,
        ),
    )
    plan = build_deployment_plan(
        policy=policy,
        options=DeployOptions(skip_existing=True),
        execute=True,
    )
    assert plan.skip_count == 4
    assert plan.import_count == 0


def test_existing_target_db_blocks_live_deploy_when_present(
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
    monkeypatch.setattr(
        "mercury.deploy.plan.fetch_user_database_names",
        lambda _cfg: ["erebus_threat_intel_prod"],
    )
    monkeypatch.setattr(
        "mercury.deploy.plan.classify_target_database",
        lambda database, **kwargs: __import__(
            "mercury.deploy.target_status", fromlist=["TargetDatabaseState"]
        ).TargetDatabaseState(
            status="exists_verified",
            exists_on_server=True,
            detail="appears healthy/verified",
            table_count=1,
        ),
    )
    plan = build_deployment_plan(
        policy=policy,
        databases=["erebus_threat_intel_prod"],
        options=DeployOptions(skip_existing=True),
        execute=True,
    )
    assert plan.candidates[0].deploy_action == "SKIP"
    assert plan.import_count == 0


def test_dry_run_deploy_does_not_execute_imports(tmp_path: Path) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    calls: list[str] = []

    def fake_runner(*args, **kwargs) -> None:
        calls.append("import")

    batch = execute_deployment_batch(policy=policy, execute=False, import_runner=fake_runner)
    assert not calls
    assert all(result.dry_run for result in batch.results)


def test_checksum_mismatch_blocks_deploy(tmp_path: Path) -> None:
    from mercury.deploy.models import DeploymentCandidate
    from mercury.deploy.runner import execute_deployment_for_candidate

    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=_usb_policy(tmp_path).backup_root,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    backup_dir = _write_verified_backup(
        policy.backup_root,
        "erebus_threat_intel_prod",
        backup_id="bad",
        created_at="2026-06-09T12:00:00+00:00",
    )
    (backup_dir / "checksum.sha256").write_text("deadbeef  wrong.sql.gz\n", encoding="utf-8")
    dump_name = "erebus_threat_intel_prod_bad.sql.gz"
    candidate = DeploymentCandidate(
        source_database="erebus_threat_intel_prod",
        target_database="erebus_threat_intel_prod",
        backup_directory=str(backup_dir),
        backup_id="bad",
        dump_path=str(backup_dir / dump_name),
        manifest_path=str(backup_dir / "manifest.json"),
        checksum_path=str(backup_dir / "checksum.sha256"),
        verified=False,
    )
    result = execute_deployment_for_candidate(
        candidate=candidate,
        execute=True,
        policy=policy,
    )
    assert "checksum" in result.message.lower() or "verification failed" in result.message.lower()


def test_sql_gz_import_command_constructed_safely() -> None:
    preview = build_import_shell_preview("erebus_threat_intel_prod", "/tmp/a.sql.gz")
    assert preview.startswith("gunzip -c ")
    assert "erebus_threat_intel_prod" in preview
    commands, _ = planned_import_commands(
        target_database="erebus_threat_intel_prod",
        dump_path="/tmp/a.sql.gz",
        options=DeployOptions(),
        exists_on_server=False,
    )
    assert any("gunzip -c" in command for command in commands)


def test_deployment_report_written_after_successful_mock_deploy(
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
    config = MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="linuxadmin",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    monkeypatch.setattr("mercury.deploy.runner.try_load_mariadb_config", lambda: config)
    monkeypatch.setattr(
        "mercury.deploy.plan.run_deployment_preflight",
        lambda **kwargs: __import__(
            "mercury.deploy.models", fromlist=["DeploymentPreflight"]
        ).DeploymentPreflight(hostname="test", ready=True),
    )
    monkeypatch.setattr("mercury.deploy.plan.fetch_user_database_names", lambda _cfg: [])
    monkeypatch.setattr(
        "mercury.backup.freshness.backup_stale_handoff_blocker",
        lambda database, **kwargs: None,
    )

    def fake_sql(_cfg, sql: str) -> None:
        return None

    def fake_runner(*args, **kwargs) -> None:
        return None

    def fake_row(_cfg, _sql: str):
        return (1, 2, 0, 1024)

    batch = execute_deployment_batch(
        policy=policy,
        databases=["erebus_threat_intel_prod"],
        execute=True,
        sql_runner=fake_sql,
        import_runner=fake_runner,
        inspect_row_fn=fake_row,
    )
    assert batch.deployed_count == 1
    assert batch.report_path is not None
    assert Path(batch.report_path).is_file()


def test_verification_basic_only_without_row_counts(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"database": "erebus_threat_intel_prod"}), encoding="utf-8")
    config = MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="linuxadmin",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    result = verify_deployed_database(
        "erebus_threat_intel_prod",
        manifest_path=manifest,
        config=config,
        row_fn=lambda _cfg, _sql: (1, 3, 0, 4096),
    )
    assert "basic verification only" in result.detail
    assert result.verified is True


def test_verification_compares_row_counts_when_manifest_has_them(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "database": "erebus_threat_intel_prod",
                "row_counts": {"t1": 1, "t2": 2, "t3": 3},
            }
        ),
        encoding="utf-8",
    )
    config = MariaDbConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="linuxadmin",
        password="",
        use_client=True,
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    ok = verify_deployed_database(
        "erebus_threat_intel_prod",
        manifest_path=manifest,
        config=config,
        row_fn=lambda _cfg, _sql: (1, 3, 0, 4096),
    )
    assert "row-count inventory" in ok.detail
    assert ok.verified is True

    bad = verify_deployed_database(
        "erebus_threat_intel_prod",
        manifest_path=manifest,
        config=config,
        row_fn=lambda _cfg, _sql: (1, 1, 0, 4096),
    )
    assert not bad.verified


def test_no_drop_database_unless_explicit_overwrite() -> None:
    commands, _ = planned_import_commands(
        target_database="erebus_threat_intel_prod",
        dump_path="/tmp/a.sql.gz",
        options=DeployOptions(skip_existing=True),
        exists_on_server=True,
    )
    assert commands == []
    overwrite, _ = planned_import_commands(
        target_database="erebus_threat_intel_prod",
        dump_path="/tmp/a.sql.gz",
        options=DeployOptions(
            skip_existing=False,
            allow_overwrite_database=True,
            allow_drop_database=True,
        ),
        exists_on_server=True,
    )
    assert any(command.startswith("DROP DATABASE") for command in overwrite)


def test_repo_local_backup_root_blocked_for_live_deploy_plan(tmp_path: Path) -> None:
    local = tmp_path / "local.toml"
    local.write_text("[mercury]\n", encoding="utf-8")
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=REPO_ROOT / "backups",
        config_path=local,
        allow_unsafe_backup_root=False,
    )
    plan = build_deployment_plan(policy=policy, execute=True)
    assert any("repo-local" in blocker.lower() for blocker in plan.blockers)


def test_deployment_grants_detect_missing_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.deploy.privileges.readonly_scalars",
        lambda _cfg, _sql: [
            "GRANT SELECT, RELOAD, PROCESS, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER ON *.* TO `linuxadmin`@`localhost`",
        ],
    )
    ok, detail = deployment_grants_sufficient(
        MariaDbConnectionConfig(
            host="127.0.0.1",
            port=3306,
            user="linuxadmin",
            password="",
            use_client=True,
            unix_socket="/var/lib/mysql/mysql.sock",
        )
    )
    assert ok is False
    assert "CREATE" in detail


def test_dry_run_plan_treats_privilege_gap_as_warning_not_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _usb_policy(tmp_path)
    _seed_all_verified(policy)
    monkeypatch.setattr(
        "mercury.deploy.preflight.build_environment_status",
        lambda **kwargs: SimpleNamespace(
            config=SimpleNamespace(initialized=True, local_toml_present=True),
            mariadb=SimpleNamespace(
                service_state="active",
                mariadb_client_found=True,
                connection_works=True,
                configured_user="linuxadmin",
            ),
        ),
    )
    monkeypatch.setattr(
        "mercury.deploy.preflight.try_load_mariadb_config",
        lambda: MariaDbConnectionConfig(
            host="127.0.0.1",
            port=3306,
            user="linuxadmin",
            password="",
            use_client=True,
            unix_socket="/var/lib/mysql/mysql.sock",
        ),
    )
    monkeypatch.setattr(
        "mercury.deploy.plan.try_load_mariadb_config",
        lambda: MariaDbConnectionConfig(
            host="127.0.0.1",
            port=3306,
            user="linuxadmin",
            password="",
            use_client=True,
            unix_socket="/var/lib/mysql/mysql.sock",
        ),
    )
    monkeypatch.setattr(
        "mercury.deploy.preflight.deployment_grants_sufficient",
        lambda _cfg: (False, "missing grants: CREATE"),
    )
    monkeypatch.setattr(
        "mercury.deploy.preflight.fetch_user_database_names",
        lambda _cfg: [],
    )
    plan = build_deployment_plan(policy=policy, execute=False)
    assert plan.candidates
    assert not any("privileges" in blocker.lower() for blocker in plan.blockers)
    assert any("Live deploy blocked" in warning for warning in plan.warnings)


def test_doctor_grant_repair_sql_for_fresh_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.deploy.privileges.deployment_grants_sufficient",
        lambda _cfg: (False, "missing grants: CREATE"),
    )
    monkeypatch.setattr(
        "mercury.database.mariadb.session.try_load_mariadb_config",
        lambda: SimpleNamespace(user="linuxadmin"),
    )
    report = DoctorReport(
        repo_root=Path("/tmp/mercury"),
        current_user="linuxadmin",
        python_version="3.12",
        platform_label="Fedora",
        config=SimpleNamespace(missing_labels=(), local_toml_present=True),
        usb=SimpleNamespace(mounted=True, mercury_layout_present=True, mount_path=Path("/mnt/MERCURY_DATA_USB")),
        mariadb=SimpleNamespace(
            service_state="active",
            config_present=True,
            connection_works=True,
            configured_user="linuxadmin",
        ),
        policy=SimpleNamespace(config_path=Path("/tmp/local.toml")),
        source_databases=[
            SimpleNamespace(name="erebus_threat_intel_prod", present=False),
        ],
        verified_backup_count=3,
        verified_backup_total=3,
    )
    plan = build_repair_plan(report)
    text = "\n".join(cmd for _title, cmds in plan for cmd in cmds)
    assert "deploy system --dry-run" in text
    assert "option 8" in text
    assert "GRANT CREATE" in text
    assert deployment_grant_repair_sql("linuxadmin")[1].startswith("GRANT CREATE")


def test_deploy_plan_blocks_stale_backup_on_live_execute(
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
    _write_verified_backup(
        policy.backup_root,
        "erebus_threat_intel_prod",
        backup_id="stale-backup",
        created_at="2026-06-09T12:00:00+00:00",
    )
    monkeypatch.setattr("mercury.core.runtime.should_probe_database_status", lambda: True)
    monkeypatch.setattr(
        "mercury.backup.freshness.backup_stale_handoff_blocker",
        lambda database, **kwargs: f"Latest verified backup for '{database}' is stale relative to live source activity.",
    )
    monkeypatch.setattr(
        "mercury.deploy.plan.fetch_user_database_names",
        lambda _cfg: [],
    )
    monkeypatch.setattr(
        "mercury.deploy.plan.classify_target_database",
        lambda database, **kwargs: __import__(
            "mercury.deploy.target_status", fromlist=["TargetDatabaseState"]
        ).TargetDatabaseState(
            status="missing",
            exists_on_server=False,
            detail="missing",
            table_count=0,
        ),
    )
    plan = build_deployment_plan(
        policy=policy,
        execute=True,
        databases=["erebus_threat_intel_prod"],
    )
    assert plan.import_count == 0
    assert any("stale" in blocker.lower() for blocker in plan.blockers)


def test_handoff_lane_appears_in_menu() -> None:
    keys = {item.key for _section, items in menu_display.MENU_SECTIONS for item in items}
    assert "9" in keys
    assert menu_actions()["10"].title == "Workstation handoff"
