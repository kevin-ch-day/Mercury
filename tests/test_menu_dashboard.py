"""Tests for main menu dashboard."""

from pathlib import Path
from types import SimpleNamespace

from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.paths import REPO_ROOT
from mercury.core.platform import PlatformInfo
from mercury.menu.dashboard import _sync_readiness_summary, dashboard_rows


def test_dashboard_rows_include_core_fields() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Active writer" in text or "Backup target" in text
    assert "Execution mode" not in text
    assert "Backup mode" not in text
    assert "Execution Safety" not in text


def test_dashboard_rows_include_extended_stats() -> None:
    rows = dashboard_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Database backups" in text
    assert "Sync readiness" in text
    assert "Cutover blockers" in text
    assert "Storage mirror" in text


def test_dashboard_rows_warn_on_repo_local_backup_root(monkeypatch) -> None:
    from types import SimpleNamespace

    from mercury.core.environment_status import ConfigSetupStatus, UsbDiscovery

    repo_backups = REPO_ROOT / "backups"
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=repo_backups,
        config_path=REPO_ROOT / "config" / "local.toml",
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
    rows = dashboard_rows(probe_database=False)
    assert any("repo-local fallback" in row for row in rows)


def test_dashboard_rows_show_platform_when_not_fedora(monkeypatch) -> None:
    monkeypatch.setattr(
        "mercury.menu.dashboard.detect_platform",
        lambda: PlatformInfo(system="Windows", release="11"),
    )
    rows = dashboard_rows(probe_database=False)
    assert any("Platform" in row and "Windows supported" in row for row in rows)


def test_dashboard_rows_show_protection_incomplete_when_stale_and_missing(monkeypatch) -> None:
    from mercury.core.environment_status import ConfigSetupStatus, UsbDiscovery

    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=REPO_ROOT / "backups",
        config_path=REPO_ROOT / "config" / "local.toml",
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
        "mercury.menu.dashboard._verified_source_summary",
        lambda **kwargs: (
            {"erebus_threat_intel_prod", "scytaledroid_core_prod", "android_permission_intel"},
            {
                "erebus_threat_intel_prod",
                "scytaledroid_core_prod",
                "android_permission_intel",
                "obsidiandroid_core_prod",
            },
            {"android_permission_intel"},
            set(),
            0,
            0,
            0,
            1,
            None,
        ),
    )
    monkeypatch.setattr(
        "mercury.menu.dashboard._sync_readiness_summary",
        lambda **kwargs: (2, 0, "None."),
    )
    monkeypatch.setattr(
        "mercury.menu.dashboard._deploy_target_summary",
        lambda **kwargs: "3 of 4 protected sources on server; 1 missing",
    )
    text = "\n".join(dashboard_rows(probe_database=True))
    assert "Database backups" in text
    assert "3 of 3 server sources verified" in text
    assert "Protection incomplete: 1 stale backup" in text
    assert "catalog source absent from this server" in text


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
