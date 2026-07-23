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
from mercury.menu.dashboard import _sync_readiness_summary, dashboard_rows


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


def _first_run_env(tmp_path: Path) -> tuple[ExecutionPolicy, SimpleNamespace]:
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
    return policy, env


def _install_first_run_dashboard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy, env = _first_run_env(tmp_path)
    monkeypatch.setattr("mercury.menu.dashboard.build_environment_status", lambda **kwargs: env)
    monkeypatch.setattr("mercury.menu.dashboard.load_execution_policy", lambda: policy)


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
    monkeypatch.setattr("mercury.menu.dashboard.load_execution_policy", lambda: policy)
    monkeypatch.setattr(
        "mercury.migration.readiness.build_migration_readiness",
        lambda **kwargs: _migration_report(),
    )
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Writer" in text or "Active writer" in text or "Package" in text
    assert "Phase" in text or "Package" in text


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
    monkeypatch.setattr("mercury.menu.dashboard.load_execution_policy", lambda: policy)
    monkeypatch.setattr(
        "mercury.core.runtime.load_execution_policy",
        lambda: policy,
    )
    monkeypatch.setattr("mercury.migration.readiness.build_migration_readiness", lambda **kwargs: _migration_report())
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Package" in text or "Migration package" in text
    assert len(rows) <= 6


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
    assert "Writer" in text or "Package" in text or "Phase" in text
    assert "writer=legacy" not in text
    assert "[2]" not in text


def test_sync_readiness_summary_reports_none_verified() -> None:
    report = SimpleNamespace(
        entries=[
            SimpleNamespace(
                prod="erebus_threat_intel_prod",
                blockers=["No on-disk backup found for production source."],
            ),
            SimpleNamespace(
                prod="scytaledroid_core_prod",
                blockers=["No on-disk backup found for production source."],
            ),
        ],
        ready_count=0,
        blocked_count=2,
    )

    def fake_build_sync_readiness_report(*, live: bool):
        return report

    import mercury.sync.readiness as readiness

    original = readiness.build_sync_readiness_report
    readiness.build_sync_readiness_report = fake_build_sync_readiness_report
    try:
        ready, blocked, blocker = _sync_readiness_summary(
            live=True,
            verified_names=set(),
            source_names={
                "android_permission_intel",
                "erebus_threat_intel_prod",
                "scytaledroid_core_prod",
            },
        )
    finally:
        readiness.build_sync_readiness_report = original

    assert (ready, blocked) == (0, 2)
    assert blocker == "No verified full backups exist yet."


def test_sync_readiness_summary_reports_missing_sync_sources_when_shared_authority_verified() -> None:
    report = SimpleNamespace(
        entries=[
            SimpleNamespace(
                prod="erebus_threat_intel_prod",
                blockers=["No on-disk backup found for production source."],
            ),
            SimpleNamespace(
                prod="scytaledroid_core_prod",
                blockers=["No on-disk backup found for production source."],
            ),
        ],
        ready_count=0,
        blocked_count=2,
    )

    def fake_build_sync_readiness_report(*, live: bool):
        return report

    import mercury.sync.readiness as readiness

    original = readiness.build_sync_readiness_report
    readiness.build_sync_readiness_report = fake_build_sync_readiness_report
    try:
        _ready, _blocked, blocker = _sync_readiness_summary(
            live=True,
            verified_names={"android_permission_intel"},
            source_names={
                "android_permission_intel",
                "erebus_threat_intel_prod",
                "scytaledroid_core_prod",
            },
        )
    finally:
        readiness.build_sync_readiness_report = original

    assert blocker == "Verified backups missing for production sync sources."


def test_sync_readiness_summary_reports_missing_source_databases_for_partial_general_coverage() -> None:
    report = SimpleNamespace(
        entries=[
            SimpleNamespace(
                prod="erebus_threat_intel_prod",
                blockers=["No on-disk backup found for production source."],
            ),
            SimpleNamespace(
                prod="scytaledroid_core_prod",
                blockers=["No on-disk backup found for production source."],
            ),
        ],
        ready_count=0,
        blocked_count=2,
    )

    def fake_build_sync_readiness_report(*, live: bool):
        return report

    import mercury.sync.readiness as readiness

    original = readiness.build_sync_readiness_report
    readiness.build_sync_readiness_report = fake_build_sync_readiness_report
    try:
        _ready, _blocked, blocker = _sync_readiness_summary(
            live=True,
            verified_names={"erebus_threat_intel_prod"},
            source_names={
                "android_permission_intel",
                "erebus_threat_intel_prod",
                "scytaledroid_core_prod",
            },
        )
    finally:
        readiness.build_sync_readiness_report = original

    assert blocker == "Verified backups still missing for source databases."


def test_sync_readiness_summary_reports_stale_blocker() -> None:
    report = SimpleNamespace(
        entries=[
            SimpleNamespace(
                prod="erebus_threat_intel_prod",
                blockers=[
                    "Backup artifacts are artifact-verified but freshness is stale; "
                    "run full backup before prod→dev sync."
                ],
            ),
        ],
        ready_count=0,
        blocked_count=1,
    )

    def fake_build_sync_readiness_report(*, live: bool):
        return report

    import mercury.sync.readiness as readiness

    original = readiness.build_sync_readiness_report
    readiness.build_sync_readiness_report = fake_build_sync_readiness_report
    try:
        _ready, _blocked, blocker = _sync_readiness_summary(
            live=True,
            verified_names={"erebus_threat_intel_prod"},
            source_names={"erebus_threat_intel_prod", "android_permission_intel"},
        )
    finally:
        readiness.build_sync_readiness_report = original

    assert blocker == "Artifact-verified backups are stale; run full backup before sync."
