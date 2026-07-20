from __future__ import annotations

from mercury.migration.models import (
    MigrationCheck,
    MigrationCheckState,
    MigrationOverallStatus,
    MigrationReadinessReport,
)
from mercury.migration.terminal import next_check


def _check(check_id: str, state: MigrationCheckState, *, blocking: bool = False) -> MigrationCheck:
    return MigrationCheck(
        id=check_id,
        label=check_id,
        state=state,
        severity=state.value,
        summary=check_id,
        recommended_action=check_id,
        recommended_command=f"mercury migration {check_id}",
        blocking=blocking,
    )


def _report(*checks: MigrationCheck) -> MigrationReadinessReport:
    return MigrationReadinessReport(
        policy_state="not_started",
        observed_mirror="verified",
        operator_phase="host capture pending",
        checks=checks,
    )


def test_readiness_aggregation_all_pass() -> None:
    assert _report(_check("storage_mirror", MigrationCheckState.PASS)).overall_status == MigrationOverallStatus.PASS


def test_readiness_aggregation_warnings_only() -> None:
    assert _report(_check("duplicate_primary_mount", MigrationCheckState.WARNING)).overall_status == MigrationOverallStatus.PASS_WITH_WARNINGS


def test_readiness_aggregation_action_and_not_checked() -> None:
    assert _report(_check("runtime", MigrationCheckState.NOT_CHECKED)).overall_status == MigrationOverallStatus.ACTION_NEEDED
    assert _report(_check("web", MigrationCheckState.ACTION_NEEDED)).overall_status == MigrationOverallStatus.ACTION_NEEDED


def test_readiness_aggregation_required_failure_is_blocked() -> None:
    report = _report(_check("destination_validation", MigrationCheckState.NOT_CHECKED, blocking=True))
    assert report.overall_status == MigrationOverallStatus.BLOCKED


def test_policy_state_and_observed_evidence_are_separate() -> None:
    report = _report(_check("storage_mirror", MigrationCheckState.PASS))
    assert report.policy_state == "not_started"
    assert report.observed_mirror == "verified"
    assert report.operator_phase == "host capture pending"


def test_next_action_prefers_dirty_worktree_before_destination_and_cutover() -> None:
    report = _report(
        _check("destination_validation", MigrationCheckState.NOT_CHECKED, blocking=True),
        _check("writer_cutover_implementation", MigrationCheckState.BLOCKED, blocking=True),
        _check("erebus_web_worktree", MigrationCheckState.ACTION_NEEDED),
    )
    assert next_check(report).id == "erebus_web_worktree"


def test_next_action_prioritizes_runtime_before_scope_and_destination() -> None:
    report = _report(
        _check("destination_validation", MigrationCheckState.NOT_CHECKED, blocking=True),
        _check("obsidiandroid_core_prod", MigrationCheckState.DECISION_NEEDED),
        _check("web_runtime_configuration", MigrationCheckState.NOT_CHECKED),
    )
    assert next_check(report).id == "web_runtime_configuration"


def test_post_cutover_priority_starts_with_archive_receipt_not_cutover() -> None:
    report = _report(
        _check("usb_archive_receipt", MigrationCheckState.ACTION_NEEDED),
        _check("repository_bundles", MigrationCheckState.ACTION_NEEDED),
        _check("writer_cutover_implementation", MigrationCheckState.PASS),
    )
    assert next_check(report).id == "usb_archive_receipt"


def test_web_worktree_checks_report_dirty_and_runtime_not_checked(monkeypatch, tmp_path) -> None:
    from mercury.repo.config import RepoDefinition
    from mercury.repo.status import RepoStatus
    from mercury.migration.readiness import _repo_checks

    erebus = tmp_path / "erebus-web"
    scytale = tmp_path / "ScytaleDroid-Web"
    statuses = [
        RepoStatus(key="erebus_web", display_name="Erebus Web", path=erebus, dirty=True, untracked_count=2),
        RepoStatus(key="scytaledroid_web", display_name="ScytaleDroid Web", path=scytale, dirty=False),
    ]
    monkeypatch.setattr(
        "mercury.repo.load_repo_definitions",
        lambda: [
            RepoDefinition(key="erebus_web", display_name="Erebus Web", path=erebus),
            RepoDefinition(key="scytaledroid_web", display_name="ScytaleDroid Web", path=scytale),
        ],
    )
    monkeypatch.setattr("mercury.repo.inspect_repositories", lambda _repos: statuses)

    erebus_check, scytale_check, runtime_check, _bundles = _repo_checks()

    assert erebus_check.state == MigrationCheckState.ACTION_NEEDED
    assert scytale_check.state == MigrationCheckState.PASS
    assert runtime_check.state == MigrationCheckState.NOT_CHECKED


def test_dirty_non_web_snapshot_is_not_a_readiness_action(monkeypatch, tmp_path) -> None:
    from mercury.repo.config import RepoDefinition
    from mercury.repo.status import RepoStatus
    from mercury.migration.readiness import _repo_checks

    mercury = tmp_path / "Mercury"
    statuses = [RepoStatus(key="mercury", display_name="Mercury", path=mercury, dirty=True, untracked_count=1)]
    monkeypatch.setattr("mercury.repo.load_repo_definitions", lambda: [RepoDefinition(key="mercury", display_name="Mercury", path=mercury)])
    monkeypatch.setattr("mercury.repo.inspect_repositories", lambda _repos: statuses)
    monkeypatch.setattr("mercury.migration.web_capture.snapshot_status", lambda _repo: ("current", True))

    _erebus, _scytale, _runtime, repositories = _repo_checks()
    assert repositories.state == MigrationCheckState.PASS
    assert "current restore-checked" in repositories.summary


def test_migration_excluded_dirty_repo_does_not_block_readiness(monkeypatch, tmp_path) -> None:
    from mercury.repo.config import RepoDefinition
    from mercury.repo.status import RepoStatus
    from mercury.migration.readiness import _repo_checks

    scripts = tmp_path / "fedora-linux-scripts"
    definitions = [RepoDefinition(key="fedora_linux_scripts", display_name="Fedora Linux Scripts", path=scripts, migration_scope=False)]
    statuses = [RepoStatus(key="fedora_linux_scripts", display_name="Fedora Linux Scripts", path=scripts, dirty=True)]
    monkeypatch.setattr("mercury.repo.load_repo_definitions", lambda: definitions)
    monkeypatch.setattr("mercury.repo.inspect_repositories", lambda _repos: statuses)

    _erebus, _scytale, _runtime, repositories = _repo_checks()
    assert repositories.state == MigrationCheckState.PASS
    assert "No uncaptured" in repositories.summary
