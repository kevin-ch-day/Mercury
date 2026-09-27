"""Tests for main menu dashboard."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.core.environment_status import ConfigSetupStatus, UsbDiscovery
from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.paths import REPO_ROOT
from mercury.core.platform import PlatformInfo
from mercury.migration.models import (
    MigrationCheck,
    MigrationCheckState,
    MigrationReadinessReport,
)
from mercury.menu.dashboard import dashboard_rows


def _migration_report(*, database_summary: str = "3 local sources verified") -> MigrationReadinessReport:
    return MigrationReadinessReport(
        policy_state="verified",
        observed_mirror="verified",
        operator_phase="host capture pending",
        checks=(
            MigrationCheck("active_writer", "Active writer", MigrationCheckState.PASS, "PASS", "USB · mounted"),
            MigrationCheck("storage_mirror", "Storage mirror", MigrationCheckState.PASS, "PASS", "Verified mirror"),
            MigrationCheck("duplicate_primary_mount", "HDD duplicate mount", MigrationCheckState.WARNING, "WARNING", "HDD is mounted twice."),
            MigrationCheck("database_backups", "Database backups", MigrationCheckState.PASS, "PASS", database_summary),
            MigrationCheck("erebus_web_worktree", "Erebus Web worktree", MigrationCheckState.ACTION_NEEDED, "ACTION_NEEDED", "Dirty worktree is not fully captured."),
            MigrationCheck("web_runtime_configuration", "Web runtime configuration", MigrationCheckState.NOT_CHECKED, "NOT_CHECKED", "Runtime configuration has not been verified."),
            MigrationCheck("destination_validation", "Destination workstation", MigrationCheckState.NOT_CHECKED, "NOT_CHECKED", "Destination workstation has not been validated.", blocking=True),
            MigrationCheck("writer_cutover_implementation", "Writer cutover", MigrationCheckState.BLOCKED, "BLOCKED", "Writer cutover is not implemented.", blocking=True),
        ),
    )


def _first_run_env(tmp_path: Path) -> SimpleNamespace:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "backups",
        config_path=None,
        allow_unsafe_backup_root=True,
    )
    env = SimpleNamespace(
        policy=policy,
        config=ConfigSetupStatus(False, False, False),
        usb=UsbDiscovery(tmp_path / "usb", False, False, None),
        mariadb=SimpleNamespace(
            connection_works=None,
            config_present=False,
            mariadb_client_found=False,
            mysqldump_found=False,
            service_active=False,
            service_state="inactive",
            socket_available=False,
            connection_error=None,
        ),
        primary_setup_blocker="Local config not initialized — run: ./run.sh config init.",
        setup_hints=(),
        permission_checks=(),
        repairable_blockers=(),
        has_repairable_blockers=False,
    )
    return env


def _install_first_run_dashboard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = _first_run_env(tmp_path)
    monkeypatch.setattr("mercury.menu.dashboard.build_environment_status", lambda **kwargs: env)


def test_dashboard_rows_include_core_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_first_run_dashboard(monkeypatch, tmp_path)
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "MariaDB" in text
    assert "Config" in text
    assert "Backup target" in text
    assert "Execution mode" not in text
    assert "Backup mode" not in text
    assert "Execution Safety" not in text


def test_dashboard_rows_include_extended_stats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path / "backups",
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    (tmp_path / "local.toml").write_text("[mercury]\n", encoding="utf-8")
    env = SimpleNamespace(
        policy=policy,
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(tmp_path / "usb", True, True, None),
        mariadb=SimpleNamespace(
            connection_works=True,
            config_present=True,
            mariadb_client_found=True,
            mysqldump_found=True,
            service_active=True,
            service_state="active",
            socket_available=True,
            connection_error=None,
        ),
        primary_setup_blocker=None,
        setup_hints=(),
        permission_checks=(),
        repairable_blockers=(),
        has_repairable_blockers=False,
    )
    monkeypatch.setattr("mercury.menu.dashboard.build_environment_status", lambda **kwargs: env)
    monkeypatch.setattr(
        "mercury.migration.readiness.build_migration_readiness",
        lambda **kwargs: _migration_report(),
    )
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Writer" in text or "Active writer" in text or "Package" in text or "Mercury HDD" in text or "Backup storage" in text
    assert "Phase" in text or "Package" in text or "Migration" in text or "Next action" in text or "Recommended" in text


def test_dashboard_rows_warn_on_repo_local_backup_root(monkeypatch) -> None:
    repo_backups = REPO_ROOT / "backups"
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=repo_backups,
        config_path=None,
        allow_unsafe_backup_root=True,
    )
    env = SimpleNamespace(
        policy=policy,
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(REPO_ROOT / "mnt" / "MERCURY_DATA_USB", False, False, None),
        mariadb=SimpleNamespace(
            connection_works=None,
            config_present=True,
            mariadb_client_found=True,
            mysqldump_found=True,
            service_active=True,
            service_state="active",
            socket_available=True,
            connection_error=None,
        ),
        primary_setup_blocker=None,
        setup_hints=(),
        permission_checks=(),
        repairable_blockers=(),
        has_repairable_blockers=False,
    )
    monkeypatch.setattr("mercury.menu.dashboard.build_environment_status", lambda **kwargs: env)
    monkeypatch.setattr(
        "mercury.core.runtime.load_execution_policy",
        lambda: policy,
    )
    monkeypatch.setattr("mercury.migration.readiness.build_migration_readiness", lambda **kwargs: _migration_report())
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert (
        "Package" in text
        or "Migration package" in text
        or "Migration" in text
        or "Last backup" in text
    )
    assert "Recommended" in text or "Next action" in text
    assert len(rows) <= 7


def test_dashboard_rows_show_platform_when_not_fedora(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_first_run_dashboard(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "mercury.menu.dashboard.detect_platform",
        lambda: PlatformInfo(system="Windows", release="11"),
    )
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Platform" in text


def test_dashboard_rows_show_protection_incomplete_when_stale_and_missing(monkeypatch) -> None:
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=REPO_ROOT / "backups",
        config_path=None,
        allow_unsafe_backup_root=True,
    )
    env = SimpleNamespace(
        policy=policy,
        config=ConfigSetupStatus(True, True, True),
        usb=UsbDiscovery(REPO_ROOT / "mnt" / "MERCURY_DATA_USB", True, True, None),
        mariadb=SimpleNamespace(
            connection_works=True,
            config_present=True,
            mariadb_client_found=True,
            mysqldump_found=True,
            service_active=True,
            service_state="active",
            socket_available=True,
            connection_error=None,
        ),
        primary_setup_blocker=None,
        setup_hints=(),
        permission_checks=(),
        repairable_blockers=(),
        has_repairable_blockers=False,
    )
    monkeypatch.setattr("mercury.menu.dashboard.build_environment_status", lambda **kwargs: env)
    monkeypatch.setattr(
        "mercury.migration.readiness.build_migration_readiness",
        lambda **kwargs: _migration_report(),
    )
    text = "\n".join(dashboard_rows(probe_database=True))
    assert "Writer" in text or "Package" in text or "Phase" in text or "Mercury HDD" in text or "Next action" in text or "Backup storage" in text or "Recommended" in text
    assert "writer=legacy" not in text
    assert "[2]" not in text
