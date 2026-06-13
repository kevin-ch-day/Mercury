"""Environment setup, doctor, config init, probe, and env menu tests."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import STALE_OPERATOR_REPO_PATH, STALE_REPO_HOME_SUFFIX

from mercury.config.init import _apply_repo_local_paths, init_local_config
from mercury.core.environment_status import (
    ConfigSetupStatus,
    UsbDiscovery,
    assess_config_setup,
    backup_root_unsafe_reason,
    backup_target_dashboard_label,
    build_environment_status,
    discover_usb_target,
    mariadb_dashboard_label,
    resolve_dashboard_blocker,
)
from mercury.core.execution_policy import ExecutionPolicy, load_execution_policy, REQUIRED_BACKUP_MOUNT
from mercury.core.path_permissions import PathPermissionCheck, check_path_permission
from mercury.core.paths import REPO_ROOT
from mercury.core.safety import MODE_SEED
from mercury.database.mariadb.session import MariaDbServerProbe
from mercury.env.doctor import build_repair_plan
from mercury.env.interactive_menu import _print_live_mode_guide, run_env_menu
from mercury.env.probe import EnvProbeResult, probe_environment
from mercury.env.terminal.check import build_environment_check_fields, connection_label
from mercury.menu.dashboard import dashboard_rows


def _mariadb_stub(**overrides) -> SimpleNamespace:
    defaults = dict(
        mariadb_client="/usr/bin/mariadb",
        mysqldump_client="/usr/bin/mysqldump",
        service_state="active",
        socket_available=True,
        config_present=False,
        configured_user=None,
        connection_works=None,
        connection_error=None,
        mariadb_client_found=True,
        mysqldump_found=True,
        service_active=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _env_result() -> EnvProbeResult:
    return EnvProbeResult(
        python_version="3.14.5",
        platform_system="Linux",
        platform_release="7.0.10",
        platform_support="Fedora supported",
        repo_root="/tmp",
        config_dir="/tmp/config",
        output_dir="/tmp/out",
        mode="seed",
        dry_run_only=True,
    )

# from test_environment_status.py
def test_assess_config_setup_reflects_missing_local_files(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr("mercury.core.environment_status.LOCAL_CONFIG", config_dir / "local.toml")
    monkeypatch.setattr("mercury.core.environment_status.DATABASES_LOCAL", config_dir / "databases.toml")
    monkeypatch.setattr("mercury.core.environment_status.REPOS_LOCAL", config_dir / "repos.toml")

    status = assess_config_setup()
    assert status.local_toml_present is False
    assert status.missing_labels == ("local.toml", "databases.toml", "repos.toml")

# from test_environment_status.py
def test_discover_usb_target_detects_mounted_layout(monkeypatch, tmp_path: Path) -> None:
    mount = tmp_path / "mnt" / "MERCURY_DATA_USB"
    mount.mkdir(parents=True)
    (mount / "mercury_backups").mkdir()
    (mount / "mercury_logs").mkdir()
    monkeypatch.setattr("mercury.core.environment_status.usb_mount_is_active", lambda path, **kwargs: True)

    usb = discover_usb_target(mount_path=mount)
    assert usb.mounted is True
    assert usb.mercury_layout_present is True
    assert usb.suggested_backup_root == mount / "mercury_backups"

# from test_environment_status.py
def test_backup_target_label_calls_out_usb_when_config_missing(monkeypatch, tmp_path: Path) -> None:
    repo_backups = tmp_path / "repo" / "backups"
    repo_backups.mkdir(parents=True)
    mount = tmp_path / "usb"
    mount.mkdir()
    usb = UsbDiscovery(
        mount_path=mount,
        mounted=True,
        mercury_layout_present=True,
        suggested_backup_root=mount / "mercury_backups",
    )
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=repo_backups,
        config_path=None,
    )
    label = backup_target_dashboard_label(policy, usb)
    assert "not configured" in label.lower() or "USB detected" in label

# from test_environment_status.py
def test_backup_root_unsafe_reason_explains_missing_local_config(tmp_path: Path) -> None:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "backups",
        config_path=None,
    )
    config = ConfigSetupStatus(False, False, False)
    usb = UsbDiscovery(REQUIRED_BACKUP_MOUNT, False, False, None)
    reason = backup_root_unsafe_reason(policy, config=config, usb=usb)
    assert "local config missing" in reason or "setup required" in reason

# from test_environment_status.py
def test_mariadb_dashboard_label_distinguishes_config_missing_from_service_down() -> None:
    active_no_config = SimpleNamespace(
        connection_works=None,
        config_present=False,
        mariadb_client_found=True,
        service_active=True,
        service_state="active",
        socket_available=True,
    )
    inactive = SimpleNamespace(
        connection_works=None,
        config_present=False,
        mariadb_client_found=True,
        service_active=False,
        service_state="inactive",
        socket_available=True,
    )
    assert "config missing" in mariadb_dashboard_label(active_no_config)
    assert "service stopped" in mariadb_dashboard_label(inactive)

# from test_environment_status.py
def test_build_environment_status_prioritizes_setup_blocker(monkeypatch, tmp_path: Path) -> None:
    mount = tmp_path / "usb"
    mount.mkdir()
    (mount / "mercury_backups").mkdir()
    (mount / "mercury_logs").mkdir()
    repo_backups = tmp_path / "repo" / "backups"
    repo_backups.mkdir(parents=True)

    monkeypatch.setattr("mercury.core.environment_status.LOCAL_CONFIG", tmp_path / "missing-local.toml")
    monkeypatch.setattr("mercury.core.environment_status.DATABASES_LOCAL", tmp_path / "missing-databases.toml")
    monkeypatch.setattr("mercury.core.environment_status.REPOS_LOCAL", tmp_path / "missing-repos.toml")
    monkeypatch.setattr("mercury.core.environment_status.usb_mount_is_active", lambda path, **kwargs: True)
    monkeypatch.setattr(
        "mercury.core.environment_status.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=repo_backups,
            config_path=None,
        ),
    )
    monkeypatch.setattr(
        "mercury.core.environment_status.assess_mariadb_status",
        lambda **kwargs: SimpleNamespace(
            connection_works=None,
            config_present=False,
            mariadb_client_found=True,
            mysqldump_found=True,
            service_active=True,
            service_state="active",
            socket_available=True,
            connection_error=None,
        ),
    )

    env = build_environment_status(probe_database=False)
    assert "Local config not initialized" in (env.primary_setup_blocker or "")
    assert env.setup_hints
    assert "config init" in env.setup_hints[0]

# from test_environment_status.py
def test_dashboard_rows_show_first_run_messaging(monkeypatch, tmp_path: Path) -> None:
    repo_backups = tmp_path / "backups"
    repo_backups.mkdir()
    mount = tmp_path / "usb"
    mount.mkdir()
    (mount / "mercury_backups").mkdir()
    (mount / "mercury_logs").mkdir()

    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=repo_backups,
        config_path=None,
    )
    env = SimpleNamespace(
        policy=policy,
        config=ConfigSetupStatus(False, False, False),
        usb=UsbDiscovery(mount, True, True, mount / "mercury_backups"),
        mariadb=SimpleNamespace(
            connection_works=None,
            config_present=False,
            mariadb_client_found=True,
            mysqldump_found=True,
            service_active=True,
            service_state="active",
            socket_available=True,
            connection_error=None,
        ),
        primary_setup_blocker="Local config not initialized — USB target detected at /mnt/MERCURY_DATA_USB.",
        setup_hints=("Run: ./run.sh config init", f"USB backup layout detected at {mount}."),
        permission_checks=(),
        repairable_blockers=("local config not initialized",),
        has_repairable_blockers=True,
    )

    monkeypatch.setattr("mercury.menu.dashboard.build_environment_status", lambda **kwargs: env)
    monkeypatch.setattr("mercury.menu.dashboard.load_execution_policy", lambda: policy)
    monkeypatch.setattr("mercury.menu.dashboard._verified_source_summary", lambda **kwargs: (set(), set()))
    monkeypatch.setattr(
        "mercury.menu.dashboard._sync_readiness_summary",
        lambda **kwargs: (0, 2, "No verified full backups exist yet."),
    )

    text = "\n".join(dashboard_rows(probe_database=False))
    assert "service active" in text
    assert "USB detected" in text
    assert "local config missing" in text
    assert "Local config not initialized" in text
    assert "doctor --repair-plan" in text or "config init" in text

# from test_environment_status.py
def test_resolve_dashboard_blocker_prefers_setup_over_backup_gap() -> None:
    blocker = resolve_dashboard_blocker(
        setup_blocker="Local config not initialized — run: ./run.sh config init.",
        verified_names=set(),
        source_names={"erebus_threat_intel_prod"},
        sync_blocker="No verified full backups exist yet.",
        config_initialized=False,
    )
    assert blocker.startswith("Local config not initialized")

# from test_doctor.py
def test_config_init_repo_local_when_usb_absent(tmp_path: Path) -> None:
    text = (
        'backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"\n'
        'log_dir = "/mnt/MERCURY_DATA_USB/mercury_logs"\n'
    )
    out = _apply_repo_local_paths(text)
    assert "/mnt/MERCURY_DATA_USB" not in out
    assert "backups" in out

# from test_doctor.py
def test_root_owned_log_file_blocks_directory(tmp_path: Path) -> None:
    logs = tmp_path / "mercury_logs"
    logs.mkdir()
    if os.geteuid() == 0:
        pytest.skip("cannot simulate root-owned files as root")
    log_file = logs / "mercury-2026-06-09.log"
    log_file.write_text("seed\n", encoding="utf-8")
    try:
        os.chown(log_file, 0, 0)
    except PermissionError:
        pytest.skip("cannot chown to root in this environment")
    try:
        check = check_path_permission(logs, label="USB log directory")
        assert check.needs_repair
        assert "mercury-2026-06-09.log" in check.detail
    finally:
        os.chown(log_file, os.geteuid(), os.getegid())

# from test_doctor.py
def test_fresh_rebuild_missing_dbs_are_warnings_not_blockers() -> None:
    from mercury.env.doctor import DoctorReport, _collect_blockers, _collect_warnings

    env = SimpleNamespace(
        primary_setup_blocker=None,
        repairable_blockers=(),
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(REQUIRED_BACKUP_MOUNT, True, True, REQUIRED_BACKUP_MOUNT / "mercury_backups"),
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=REQUIRED_BACKUP_MOUNT / "mercury_backups",
            config_path=Path("/tmp/local.toml"),
        ),
        mariadb=_mariadb_stub(config_present=True, configured_user="linuxadmin", connection_works=True),
    )
    report = DoctorReport(
        repo_root=REPO_ROOT,
        current_user="linuxadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=env.config,
        usb=env.usb,
        mariadb=env.mariadb,
        policy=env.policy,
        source_databases=[
            SimpleNamespace(name="erebus_threat_intel_prod", present=False),
            SimpleNamespace(name="android_permission_intel", present=False),
        ],
        verified_backup_count=3,
        verified_backup_total=3,
    )
    blockers = _collect_blockers(env, report)
    warnings = _collect_warnings(env, report)
    assert not any("missing on server" in item for item in blockers)
    assert any("Fresh rebuild" in item for item in warnings)

# from test_doctor.py
def test_doctor_repo_warnings_use_effective_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mercury.env.doctor import DoctorReport, _collect_warnings
    from mercury.repo.config import RepoDefinition

    home = tmp_path / "linuxadmin"
    (home / STALE_REPO_HOME_SUFFIX).mkdir(parents=True)
    monkeypatch.setattr("mercury.repo.path_repair.Path.home", lambda: home)

    repos = [
        RepoDefinition(
            key="mercury",
            display_name="Mercury",
            path=STALE_OPERATOR_REPO_PATH,
        ),
        RepoDefinition(
            key="missing",
            display_name="Missing Repo",
            path=home / "GitHub" / "missing-repo",
        ),
    ]
    monkeypatch.setattr("mercury.repo.load_repo_definitions", lambda: repos)

    env = SimpleNamespace(
        config=ConfigSetupStatus(True, True, True),
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=REQUIRED_BACKUP_MOUNT / "mercury_backups",
            config_path=Path("/tmp/local.toml"),
        ),
        mariadb=_mariadb_stub(config_present=True, configured_user="linuxadmin", connection_works=True),
    )
    report = DoctorReport(
        repo_root=REPO_ROOT,
        current_user="linuxadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=env.config,
        usb=UsbDiscovery(REQUIRED_BACKUP_MOUNT, True, True, REQUIRED_BACKUP_MOUNT / "mercury_backups"),
        mariadb=env.mariadb,
        policy=env.policy,
        source_databases=[],
        verified_backup_count=0,
        verified_backup_total=0,
    )
    warnings = _collect_warnings(env, report)
    assert any("Stale repository paths" in item for item in warnings)
    assert not any("Mercury" in item and "missing" in item.lower() for item in warnings)
    assert any("Missing Repo" in item for item in warnings)


# from test_doctor.py
def test_doctor_repair_plan_includes_web_directory_prep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mercury.repo.config import RepoDefinition

    repos = [
        RepoDefinition(
            key="erebus_web",
            display_name="Erebus Web",
            path=Path("/var/www/html/erebus-web"),
        ),
    ]
    repos_toml = tmp_path / "repos.toml"
    repos_toml.write_text("[repos.erebus_web]\npath = \"/var/www/html/erebus-web\"\n", encoding="utf-8")
    monkeypatch.setattr("mercury.env.doctor.REPOS_LOCAL", repos_toml)
    monkeypatch.setattr("mercury.repo.load_repo_definitions", lambda: repos)
    report = SimpleNamespace(
        repo_root=REPO_ROOT,
        current_user="linuxadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(REQUIRED_BACKUP_MOUNT, True, True, REQUIRED_BACKUP_MOUNT / "mercury_backups"),
        mariadb=_mariadb_stub(config_present=True, configured_user="linuxadmin", connection_works=True),
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=REQUIRED_BACKUP_MOUNT / "mercury_backups",
            config_path=tmp_path / "local.toml",
        ),
        permission_checks=[],
        source_databases=[
            SimpleNamespace(name="erebus_threat_intel_prod", present=False),
        ],
        verified_backup_count=3,
        verified_backup_total=3,
        blockers=[],
        warnings=[],
        self_healed=[],
        recommended_next_step="./run.sh deploy system --dry-run",
    )
    plan = build_repair_plan(report)
    titles = [title for title, _cmds in plan]
    text = "\n".join(cmd for _title, cmds in plan for cmd in cmds)
    assert any("Prepare web repository directories" in title for title in titles)
    assert "sudo mkdir -p /var/www/html" in text
    assert "sudo chown linuxadmin:linuxadmin /var/www/html" in text


# from test_doctor.py
def test_root_owned_usb_dir_needs_repair(tmp_path: Path) -> None:
    mount = tmp_path / "usb"
    logs = mount / "mercury_logs"
    logs.mkdir(parents=True)
    if os.geteuid() == 0:
        pytest.skip("cannot simulate root-owned dirs as root")
    try:
        logs.chmod(0o755)
        os.chown(logs, 0, 0)
    except PermissionError:
        pytest.skip("cannot chown to root in this environment")
    try:
        check = check_path_permission(logs, label="USB log directory")
        assert check.needs_repair or not check.writable
    finally:
        os.chown(logs, os.geteuid(), os.getegid())

# from test_doctor.py
def test_doctor_repair_plan_includes_chown_commands(tmp_path: Path) -> None:
    mount = tmp_path / "usb"
    logs = mount / "mercury_logs"
    logs.mkdir(parents=True)
    report = SimpleNamespace(
        repo_root=REPO_ROOT,
        current_user="linuxadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(mount, True, True, mount / "mercury_backups"),
        mariadb=_mariadb_stub(config_present=True, configured_user="linuxadmin", connection_works=True),
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=mount / "mercury_backups",
            config_path=tmp_path / "local.toml",
        ),
        permission_checks=[
            PathPermissionCheck(
                path=logs,
                label="USB log directory",
                exists=True,
                writable=False,
                owner="root",
                owner_mismatch=True,
                detail="not writable (owner: root)",
            )
        ],
        source_databases=[],
        verified_backup_count=0,
        verified_backup_total=3,
        blockers=["USB log directory not writable"],
        warnings=[],
        self_healed=[],
        recommended_next_step="./run.sh doctor --repair-plan",
    )
    plan = build_repair_plan(report)
    text = "\n".join(cmd for _title, cmds in plan for cmd in cmds)
    assert "repair-usb" in text
    assert "chown" in text
    assert "mercury_logs" in text

# from test_doctor.py
def test_doctor_repair_plan_mariadb_root_auth() -> None:
    report = SimpleNamespace(
        repo_root=REPO_ROOT,
        current_user="linuxadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(REQUIRED_BACKUP_MOUNT, True, True, REQUIRED_BACKUP_MOUNT / "mercury_backups"),
        mariadb=_mariadb_stub(
            config_present=True,
            configured_user="root",
            connection_works=False,
            connection_error="Access denied for user 'root'@'localhost'",
        ),
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=REQUIRED_BACKUP_MOUNT / "mercury_backups",
            config_path=Path("/tmp/local.toml"),
        ),
        permission_checks=[],
        source_databases=[],
        verified_backup_count=0,
        verified_backup_total=3,
        blockers=["MariaDB configured user cannot connect"],
        warnings=[],
        self_healed=[],
        recommended_next_step="./run.sh doctor --repair-plan",
    )
    plan = build_repair_plan(report)
    text = "\n".join(cmd for _title, cmds in plan for cmd in cmds)
    assert "CREATE USER" in text
    assert "linuxadmin" in text
    assert "unix_socket" in text

# from test_doctor.py
def test_doctor_repair_plan_usb_not_mounted() -> None:
    report = SimpleNamespace(
        repo_root=REPO_ROOT,
        current_user="linuxadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=ConfigSetupStatus(False, False, False),
        usb=UsbDiscovery(REQUIRED_BACKUP_MOUNT, False, False, None),
        mariadb=_mariadb_stub(),
        policy=ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=REPO_ROOT / "backups",
            config_path=None,
        ),
        permission_checks=[],
        source_databases=[],
        verified_backup_count=0,
        verified_backup_total=0,
        blockers=["local config not initialized"],
        warnings=[],
        self_healed=[],
        recommended_next_step="./run.sh config init",
    )
    plan = build_repair_plan(report)
    text = "\n".join(cmd for _title, cmds in plan for cmd in cmds)
    assert "repair-usb" in text
    assert "mercury_backups" in text
    assert "config init" in text

# from test_doctor.py
def test_repo_local_never_live_safe() -> None:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=REPO_ROOT / "backups",
        config_path=REPO_ROOT / "config" / "local.toml",
    )
    assert policy.live_execution_allowed() is False

# from test_config_init.py
def test_init_creates_files(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    example_db = config_dir / "databases.example.toml"
    example_db.write_text('[databases]\nfoo_prod = { host = "h", port = 1 }\n', encoding="utf-8")
    example_repos = config_dir / "repos.example.toml"
    example_repos.write_text('[repos.mercury]\npath = "/tmp/Mercury"\n', encoding="utf-8")
    example_local = config_dir / "local.example.toml"
    example_local.write_text("[mercury]\nmode = 'seed'\n", encoding="utf-8")

    local_db = config_dir / "databases.toml"
    local_repos = config_dir / "repos.toml"
    local_local = config_dir / "local.toml"

    monkeypatch.setattr("mercury.config.init.DATABASES_EXAMPLE", example_db)
    monkeypatch.setattr("mercury.config.init.DATABASES_LOCAL", local_db)
    monkeypatch.setattr("mercury.config.init.REPOS_EXAMPLE", example_repos)
    monkeypatch.setattr("mercury.config.init.REPOS_LOCAL", local_repos)
    monkeypatch.setattr("mercury.config.init.LOCAL_EXAMPLE", example_local)
    monkeypatch.setattr("mercury.config.init.LOCAL_CONFIG", local_local)
    monkeypatch.setattr(
        "mercury.config.init.discover_usb_target",
        lambda: SimpleNamespace(mercury_layout_present=False, mount_path=Path("/mnt/MERCURY_DATA_USB")),
    )

    results = init_local_config()
    assert local_db.exists()
    assert local_repos.exists()
    assert local_local.exists()
    assert any("created" in r for r in results)

# from test_config_init.py
def test_init_customizes_mariadb_user_for_os_user(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    example_local = config_dir / "local.example.toml"
    example_local.write_text(
        "[mercury]\nmode = 'seed'\n[mariadb]\nuser = \"root\"\n",
        encoding="utf-8",
    )
    local_local = config_dir / "local.toml"
    example_db = config_dir / "databases.example.toml"
    example_db.write_text("[databases]\n", encoding="utf-8")
    example_repos = config_dir / "repos.example.toml"
    example_repos.write_text("[repos.mercury]\npath = \"/tmp\"\n", encoding="utf-8")
    local_db = config_dir / "databases.toml"
    local_repos = config_dir / "repos.toml"

    monkeypatch.setattr("mercury.config.init.DATABASES_EXAMPLE", example_db)
    monkeypatch.setattr("mercury.config.init.DATABASES_LOCAL", local_db)
    monkeypatch.setattr("mercury.config.init.REPOS_EXAMPLE", example_repos)
    monkeypatch.setattr("mercury.config.init.REPOS_LOCAL", local_repos)
    monkeypatch.setattr("mercury.config.init.LOCAL_EXAMPLE", example_local)
    monkeypatch.setattr("mercury.config.init.LOCAL_CONFIG", local_local)
    monkeypatch.setattr("mercury.config.init.getpass.getuser", lambda: "linuxadmin")
    monkeypatch.setattr(
        "mercury.config.init.discover_usb_target",
        lambda: SimpleNamespace(mercury_layout_present=True, mount_path=Path("/mnt/MERCURY_DATA_USB")),
    )

    results = init_local_config()
    text = local_local.read_text(encoding="utf-8")
    assert 'user = "linuxadmin"' in text
    assert any("linuxadmin" in line for line in results)
    assert any("USB backup layout detected" in line for line in results)

# from test_env_probe.py
def test_probe_returns_expected_fields() -> None:
    result = probe_environment()
    policy = load_execution_policy()
    assert result.python_version
    assert result.platform_system
    assert result.platform_support
    assert result.repo_root
    expected_mode = MODE_SEED if not policy.backup_execution_allowed() else "operational"
    assert result.mode == expected_mode
    assert result.dry_run_only is (not policy.backup_execution_allowed())

# from test_env_probe.py
def test_probe_config_status_keys() -> None:
    result = probe_environment()
    assert "databases.toml" in result.config_status
    assert "local.toml" in result.config_status
    assert "platform_support" in result.config_status

# from test_env_probe.py
def test_probe_notes_non_empty() -> None:
    result = probe_environment()
    assert len(result.notes) >= 1

# from test_env_display.py
def test_connection_label_socket() -> None:
    probe = MariaDbServerProbe(
        host="127.0.0.1",
        port=3306,
        configured_user="root",
        connected=True,
        current_user="root@localhost",
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    assert connection_label(probe) == "root@localhost"

# from test_env_display.py
def test_build_environment_check_fields_connected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "mercury.core.execution_policy.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path / "backups",
            config_path=None,
        ),
    )
    probe = MariaDbServerProbe(
        host="localhost",
        port=3306,
        configured_user="root",
        connected=True,
        server_version="10.11.16-MariaDB",
        latency_ms=13.47,
        user_database_count=7,
        current_user="root@localhost",
        unix_socket="/var/lib/mysql/mysql.sock",
    )
    fields = build_environment_check_fields(_env_result(), probe)
    assert fields["Runtime"]["Python"] == "3.14.5"
    assert fields["Runtime"]["Platform support"] == "Fedora supported"
    assert fields["MariaDB"]["Connection"] == "connected"
    assert fields["MariaDB"]["User"] == "root@localhost"
    assert fields["MariaDB"]["Socket path"] == "/var/lib/mysql/mysql.sock"
    assert "Backup mode" in fields["Execution Safety"]
    assert "Sync/deploy/restore" in fields["Execution Safety"]
    assert "Database Scope" not in fields
    assert "Recommended action" not in fields

# from test_env_display.py
def test_build_environment_check_fields_not_configured(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr("mercury.core.environment_status.LOCAL_CONFIG", config_dir / "local.toml")
    monkeypatch.setattr("mercury.core.environment_status.DATABASES_LOCAL", config_dir / "databases.toml")
    monkeypatch.setattr("mercury.core.environment_status.REPOS_LOCAL", config_dir / "repos.toml")
    monkeypatch.setattr(
        "mercury.core.execution_policy.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=True,
            live_actions_enabled=False,
            backup_root=tmp_path / "backups",
            config_path=None,
        ),
    )
    monkeypatch.setattr(
        "mercury.core.environment_status.assess_mariadb_status",
        lambda **kwargs: SimpleNamespace(
            mariadb_client="/usr/bin/mariadb",
            mysqldump_client="/usr/bin/mysqldump",
            service_state="active",
            socket_available=True,
            config_present=False,
            connection_works=None,
            connection_error=None,
            mariadb_client_found=True,
            mysqldump_found=True,
            service_active=True,
        ),
    )
    from types import SimpleNamespace

    fields = build_environment_check_fields(_env_result())
    assert "not configured" in str(fields["MariaDB"]["Connection"])
    assert fields["Local Config"]["local.toml"] == "missing"

# from test_env_display.py
def test_build_environment_check_fields_live_sync_mentions_sync_dev(
    monkeypatch,
) -> None:
    probe = MariaDbServerProbe(
        host="localhost",
        port=3306,
        configured_user="root",
        connected=True,
        current_user="root@localhost",
    )
    monkeypatch.setattr(
        "mercury.env.terminal.check.load_execution_policy",
        lambda: ExecutionPolicy(
            dry_run=False,
            live_actions_enabled=True,
            backup_root=Path("/mnt/MERCURY_DATA_USB/mercury_backups"),
            config_path=Path("/tmp/local.toml"),
            allow_unsafe_backup_root=True,
        ),
    )
    fields = build_environment_check_fields(_env_result(), probe)
    assert fields["Execution Safety"]["Sync/deploy/restore"] == "enabled with confirmation"
    assert fields["Execution Safety"]["Backup mode"] == "writes to USB"

# from test_env_menu.py
def test_run_env_menu_non_interactive(capsys: pytest.CaptureFixture[str]) -> None:
    run_env_menu(interactive=False)
    out = capsys.readouterr().out
    assert "ENVIRONMENT CHECK" in out
    assert "Runtime" in out
    assert "Python" in out
    assert "Rescan" in out
    assert "Live mode guide" not in out
    assert "CLI:" not in out
    assert "╭" not in out
    assert "Submenu choice" not in out

# from test_env_menu.py
def test_run_env_menu_no_duplicate_heading(capsys: pytest.CaptureFixture[str]) -> None:
    run_env_menu(interactive=False)
    out = capsys.readouterr().out
    assert out.count("ENVIRONMENT CHECK") == 1

# from test_env_menu.py
def test_live_mode_guide_has_no_decorative_bullets(capsys: pytest.CaptureFixture[str]) -> None:
    _print_live_mode_guide()
    out = capsys.readouterr().out
    assert "OPERATOR SAFETY GUIDE" in out
    assert "◆" not in out
    assert "Destructive actions" in out

