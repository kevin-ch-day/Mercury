"""Tests for post-rebuild UX polish: status, doctor, dashboard, repo branches."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury.core.environment_status import resolve_dashboard_blocker
from mercury.deploy.rebuild_status import (
    build_rebuild_status_report,
    detect_leftover_databases,
    sync_blocker_is_rebuild_blocker,
)
from mercury.deploy.repos.models import RepoDeployCandidate
from mercury.deploy.repos.plan import planned_repo_commands
from mercury.deploy.repos.post_deploy import finalize_deployed_repository, recovery_branch_name
from mercury.env.doctor import DoctorReport, _recommended_next_step, _rebuild_is_complete


def test_recovery_branch_name_format() -> None:
    assert recovery_branch_name().startswith("mercury-restored-")


def test_usb_plan_uses_checkout_b_not_detach(tmp_path: Path) -> None:
    candidate = RepoDeployCandidate(
        key="erebus_web",
        display_name="Erebus Web",
        target_path=str(tmp_path / "erebus-web"),
        source="usb_bundle",
        bundle_path="/mnt/usb/erebus.bundle",
        branch="main",
        commit="abc1234",
        remote_url="https://github.com/example/erebus-web.git",
    )
    commands, skip = planned_repo_commands(candidate, options=__import__(
        "mercury.deploy.repos.models", fromlist=["RepoDeployOptions"]
    ).RepoDeployOptions())
    assert skip is None
    assert any("checkout -B main abc1234" in command for command in commands)
    assert not any(command.endswith("checkout abc1234") for command in commands)


def test_finalize_deployed_repository_sets_branch_and_origin(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(target), "commit", "--allow-empty", "-m", "seed"], check=True, capture_output=True)

    candidate = RepoDeployCandidate(
        key="demo",
        display_name="Demo",
        target_path=str(target),
        source="usb_bundle",
        branch="main",
        commit=None,
        remote_url="https://github.com/example/demo.git",
    )
    note = finalize_deployed_repository(candidate)
    assert "branch main" in note
    assert "github.com/example/demo.git" in note

    branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "main"


def test_detect_leftover_test_database() -> None:
    suggestions = detect_leftover_databases(
        {"android_permission_intel", "android_permission_intel_test", "mysql"}
    )
    assert len(suggestions) == 1
    assert "android_permission_intel_test" in suggestions[0][0]
    assert "DROP DATABASE" in suggestions[0][1]


def test_sync_blocker_not_environment_blocker_when_deploy_complete() -> None:
    assert sync_blocker_is_rebuild_blocker("Dev target missing: erebus_threat_intel_dev", deploy_complete=True) is False
    blocker = resolve_dashboard_blocker(
        setup_blocker=None,
        verified_names={"a"},
        source_names={"a"},
        sync_blocker="Dev target missing: erebus_threat_intel_dev",
        config_initialized=True,
        deploy_complete=True,
    )
    assert "rebuild complete" in blocker.lower()


def test_doctor_recommends_fresh_backup_when_rebuild_complete() -> None:
    report = DoctorReport(
        repo_root=Path("/tmp"),
        current_user="linuxadmin",
        python_version="3.14",
        platform_label="Fedora",
        config=SimpleNamespace(),
        usb=SimpleNamespace(),
        mariadb=SimpleNamespace(connection_works=True),
        policy=SimpleNamespace(
            live_execution_allowed=lambda: True,
            backup_execution_allowed=lambda: True,
        ),
        source_databases=[
            SimpleNamespace(name="erebus_threat_intel_prod", present=True),
        ],
        verified_backup_count=3,
        verified_backup_total=3,
        blockers=[],
    )
    report.rebuild_complete = _rebuild_is_complete(report)
    step = _recommended_next_step(SimpleNamespace(policy=report.policy), report)
    assert "backup all" in step


def test_db_inventory_cli_alias_registered() -> None:
    from mercury.db_commands import register_commands
    import typer

    app = typer.Typer()
    register_commands(app)
    names = {command.name for command in app.registered_commands}
    assert "inventory" in names
    assert "discover" in names


def test_build_rebuild_status_report_deploy_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.core.environment_status.build_environment_status",
        lambda **kwargs: SimpleNamespace(
            policy=SimpleNamespace(
            live_execution_allowed=lambda: True,
            backup_execution_allowed=lambda: True,
        ),
            mariadb=SimpleNamespace(connection_works=True),
            permission_checks=[],
        ),
    )
    monkeypatch.setattr(
        "mercury.deploy.rebuild_status.build_deployment_snapshot",
        lambda **kwargs: SimpleNamespace(
            on_server_count=3,
            protected_source_count=3,
            deployment_needed=False,
            verified_backup_count=3,
        ),
    )
    monkeypatch.setattr("mercury.deploy.rebuild_status.load_repo_definitions", lambda: [])
    monkeypatch.setattr("mercury.deploy.rebuild_status.inspect_repositories", lambda _repos: [])
    monkeypatch.setattr(
        "mercury.deploy.rebuild_status._sync_summary",
        lambda: (0, 2, "Dev target missing: erebus_threat_intel_dev"),
    )
    report = build_rebuild_status_report(probe_database=True)
    assert report.deploy_status == "complete"
    assert "backup all" in report.recommended_next
