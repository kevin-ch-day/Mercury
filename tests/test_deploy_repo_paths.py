"""Tests for stale repository path resolution during deploy selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.deploy.repos.selection import resolve_repo_deploy_candidates
from mercury.repo.config import RepoDefinition


def test_selection_rewrites_stale_repo_path_for_deploy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("mercury.repo.path_repair.Path.home", lambda: tmp_path / "linuxadmin")
    repos = [
        RepoDefinition(
            key="mercury",
            display_name="Mercury",
            path=Path("/home/secadmin/Laughlin/GitHub/Mercury"),
            remote_url="https://github.com/example/Mercury.git",
        )
    ]
    monkeypatch.setattr("mercury.deploy.repos.selection.load_repo_definitions", lambda: repos)
    monkeypatch.setattr("mercury.deploy.repos.selection.inspect_repositories", lambda _repos: [])
    monkeypatch.setattr(
        "mercury.deploy.repos.selection.latest_repo_manifest_entries",
        lambda _dir: {},
    )
    monkeypatch.setattr(
        "mercury.deploy.repos.selection.load_repo_bundle_settings",
        lambda: type("S", (), {"manifest_dir": tmp_path / "manifests"})(),
    )
    candidates = resolve_repo_deploy_candidates(source_mode="github")
    assert len(candidates) == 1
    assert candidates[0].target_path == str((tmp_path / "linuxadmin" / "GitHub" / "Mercury").resolve())
    assert candidates[0].configured_path is not None


def test_web_repo_parent_dirs_needing_prep_detects_var_www() -> None:
    from mercury.repo.path_repair import web_repo_parent_dirs_needing_prep

    repos = [
        RepoDefinition(key="erebus_web", display_name="Erebus Web", path=Path("/var/www/html/erebus-web")),
    ]
    parents = web_repo_parent_dirs_needing_prep(repos)
    assert parents == [Path("/var/www/html")]


def test_repo_path_missing_detail_skips_existing_effective_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mercury.repo.path_repair import repo_path_missing_detail

    home = tmp_path / "linuxadmin"
    mercury = home / "GitHub" / "Mercury"
    mercury.mkdir(parents=True)
    monkeypatch.setattr("mercury.repo.path_repair.Path.home", lambda: home)

    repo = RepoDefinition(
        key="mercury",
        display_name="Mercury",
        path=Path("/home/secadmin/Laughlin/GitHub/Mercury"),
    )
    assert repo_path_missing_detail(repo) is None
