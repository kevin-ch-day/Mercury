"""Tests for repository deployment onto a fresh workstation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mercury.core.execution_policy import ExecutionPolicy
from mercury.deploy.repos.build_plan import build_repo_deploy_plan
from mercury.deploy.repos.models import RepoDeployOptions
from mercury.deploy.repos.plan import planned_repo_commands
from mercury.deploy.repos.runner import execute_repo_deploy_batch
from mercury.deploy.repos.selection import resolve_repo_deploy_candidates
from mercury.repo.config import RepoDefinition, load_repo_definitions
from mercury.repo.manifest_index import latest_repo_manifest_entries


def _repo(
    key: str,
    path: Path,
    *,
    remote_url: str | None = None,
) -> RepoDefinition:
    return RepoDefinition(
        key=key,
        display_name=key.title(),
        path=path,
        remote_url=remote_url,
        default_branch="main",
    )


def test_planned_repo_commands_github_clone() -> None:
    from mercury.deploy.repos.models import RepoDeployCandidate

    candidate = RepoDeployCandidate(
        key="mercury",
        display_name="Mercury",
        target_path="/tmp/mercury",
        source="github",
        remote_url="https://github.com/example/Mercury.git",
        branch="main",
    )
    commands, skip = planned_repo_commands(candidate, options=RepoDeployOptions())
    assert skip is None
    assert any("git clone" in command and "github.com" in command for command in commands)


def test_planned_repo_commands_skip_existing() -> None:
    from mercury.deploy.repos.models import RepoDeployCandidate

    candidate = RepoDeployCandidate(
        key="mercury",
        display_name="Mercury",
        target_path="/tmp/mercury",
        source="github",
        remote_url="https://github.com/example/Mercury.git",
        exists_on_system=True,
    )
    commands, skip = planned_repo_commands(candidate, options=RepoDeployOptions(skip_existing=True))
    assert commands == []
    assert skip


def test_repo_deploy_plan_from_github_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "GitHub" / "mercury"
    repos = [_repo("mercury", target, remote_url="https://github.com/example/Mercury.git")]
    monkeypatch.setattr("mercury.deploy.repos.selection.load_repo_definitions", lambda: repos)
    monkeypatch.setattr("mercury.deploy.repos.selection.inspect_repositories", lambda _repos: [])
    monkeypatch.setattr("mercury.deploy.repos.build_plan.run_repo_deploy_preflight", lambda **kwargs: __import__(
        "mercury.deploy.models", fromlist=["DeploymentPreflight"]
    ).DeploymentPreflight(hostname="test", ready=True))
    plan = build_repo_deploy_plan(source_mode="github", execute=False)
    assert plan.planned_commands
    assert any("git clone" in command for command in plan.planned_commands)


def test_repo_deploy_from_usb_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "mercury.bundle"
    bundle.write_bytes(b"bundle")
    manifest_dir = tmp_path / "manifests" / "2026-06-09"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "mercury_test.repo_manifest.json").write_text(
        json.dumps(
            {
                "repo_key": "mercury",
                "generated_at": "2026-06-09T12:00:00+00:00",
                "bundle_path": str(bundle),
                "branch": "main",
                "commit": "abc123",
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "GitHub" / "mercury"
    repos = [_repo("mercury", target)]
    settings = type("S", (), {"manifest_dir": tmp_path / "manifests"})()
    monkeypatch.setattr("mercury.deploy.repos.selection.load_repo_definitions", lambda: repos)
    monkeypatch.setattr("mercury.deploy.repos.selection.load_repo_bundle_settings", lambda: settings)
    monkeypatch.setattr("mercury.deploy.repos.selection.inspect_repositories", lambda _repos: [])
    candidates = resolve_repo_deploy_candidates(source_mode="usb")
    assert len(candidates) == 1
    assert candidates[0].source == "usb_bundle"
    commands, _ = planned_repo_commands(candidates[0], options=RepoDeployOptions())
    assert any("git clone" in command and "mercury.bundle" in command for command in commands)


def test_latest_repo_manifest_entries_picks_newest(tmp_path: Path) -> None:
    older = tmp_path / "2026-06-08"
    newer = tmp_path / "2026-06-09"
    older.mkdir()
    newer.mkdir()
    (older / "mercury_old.repo_manifest.json").write_text(
        json.dumps({"repo_key": "mercury", "generated_at": "2026-06-08T10:00:00+00:00"}),
        encoding="utf-8",
    )
    (newer / "mercury_new.repo_manifest.json").write_text(
        json.dumps({"repo_key": "mercury", "generated_at": "2026-06-09T12:00:00+00:00", "bundle_path": "/x"}),
        encoding="utf-8",
    )
    latest = latest_repo_manifest_entries(tmp_path)
    assert latest["mercury"]["bundle_path"] == "/x"


def test_dry_run_repo_deploy_does_not_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "mercury"
    repos = [_repo("mercury", target, remote_url="https://github.com/example/Mercury.git")]
    monkeypatch.setattr("mercury.deploy.repos.selection.load_repo_definitions", lambda: repos)
    monkeypatch.setattr("mercury.deploy.repos.selection.inspect_repositories", lambda _repos: [])
    calls: list[list[str]] = []

    def fake_runner(argv: list[str]) -> None:
        calls.append(argv)

    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
        allow_unsafe_backup_root=True,
    )
    batch = execute_repo_deploy_batch(
        policy=policy,
        source_mode="github",
        execute=False,
        clone_runner=fake_runner,
    )
    assert not calls
    assert all(result.dry_run for result in batch.results)
