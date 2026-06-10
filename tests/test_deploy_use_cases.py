"""Tests for deployment use cases and repository path repair."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.deploy.use_cases import detect_deploy_use_cases
from mercury.repo.config import RepoDefinition
from mercury.repo.path_repair import (
    repo_path_is_stale,
    resolve_effective_repo_path,
    rewrite_stale_repo_path,
)
from mercury.repo.usb_metadata import enrich_repo_definitions_from_usb
from tests.conftest import STALE_OPERATOR_REPO_PATH, STALE_REPO_HOME_SUFFIX


def test_rewrite_stale_repo_path_to_current_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("mercury.repo.path_repair.Path.home", lambda: tmp_path / "linuxadmin")
    stored = STALE_OPERATOR_REPO_PATH
    effective = rewrite_stale_repo_path(stored)
    assert effective == (tmp_path / "linuxadmin" / STALE_REPO_HOME_SUFFIX).resolve()


def test_resolve_effective_repo_path_prefers_existing(tmp_path: Path) -> None:
    existing = tmp_path / "Mercury"
    existing.mkdir(parents=True)
    resolved = resolve_effective_repo_path(existing)
    assert resolved == existing.resolve()


def test_repo_path_is_stale_detects_secadmin_prefix() -> None:
    assert repo_path_is_stale(Path("/home/secadmin/Laughlin/GitHub/Mercury"))
    assert not repo_path_is_stale(Path("/home/linuxadmin/GitHub/Mercury"))


def test_enrich_repo_definitions_from_usb(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifests" / "2026-06-09"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "mercury_x.repo_manifest.json").write_text(
        '{"repo_key":"mercury","generated_at":"2026-06-09T12:00:00+00:00","remote_url":"https://github.com/example/Mercury.git","branch":"main"}',
        encoding="utf-8",
    )
    from mercury.repo.config import RepoBundleSettings

    settings = RepoBundleSettings(manifest_dir=tmp_path / "manifests")
    repos = enrich_repo_definitions_from_usb(
        [RepoDefinition(key="mercury", display_name="Mercury", path=tmp_path / "Mercury")],
        settings=settings,
    )
    assert repos[0].remote_url == "https://github.com/example/Mercury.git"


def test_detect_deploy_use_cases_includes_stale_repos_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.deploy.use_cases.load_repo_definitions",
        lambda: [
            RepoDefinition(
                key="mercury",
                display_name="Mercury",
                path=STALE_OPERATOR_REPO_PATH,
            )
        ],
    )
    monkeypatch.setattr(
        "mercury.deploy.use_cases.resolve_repo_deploy_candidates",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "mercury.deploy.use_cases.build_deployment_snapshot",
        lambda **kwargs: type(
            "S",
            (),
            {
                "import_count": 0,
                "on_server_count": 0,
                "deployment_needed": False,
                "skip_count": 0,
                "verified_backup_count": 0,
                "protected_source_count": 0,
                "block_count": 0,
                "summary_message": None,
                "candidates": (),
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.deploy.use_cases.build_repo_deploy_plan",
        lambda **kwargs: type("P", (), {"planned_commands": [], "blockers": []})(),
    )
    cases = detect_deploy_use_cases()
    assert any(case.case_id == "stale_repos_config" for case in cases)


def test_detect_deploy_use_cases_includes_web_directory_prep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "mercury.deploy.use_cases.load_repo_definitions",
        lambda: [
            RepoDefinition(
                key="erebus_web",
                display_name="Erebus Web",
                path=Path("/var/www/html/erebus-web"),
            )
        ],
    )
    monkeypatch.setattr("mercury.deploy.use_cases.resolve_repo_deploy_candidates", lambda **kwargs: [])
    monkeypatch.setattr(
        "mercury.deploy.use_cases.build_deployment_snapshot",
        lambda **kwargs: type(
            "S",
            (),
            {
                "import_count": 0,
                "on_server_count": 0,
                "deployment_needed": False,
                "skip_count": 0,
                "verified_backup_count": 0,
                "protected_source_count": 0,
                "block_count": 0,
                "summary_message": None,
                "candidates": (),
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.deploy.use_cases.build_repo_deploy_plan",
        lambda **kwargs: type("P", (), {"planned_commands": [], "blockers": []})(),
    )
    cases = detect_deploy_use_cases()
    assert any(case.case_id == "web_repo_directories" for case in cases)


def test_detect_deploy_use_cases_when_databases_already_on_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mercury.deploy.use_cases.load_repo_definitions", lambda: [])
    monkeypatch.setattr("mercury.deploy.use_cases.resolve_repo_deploy_candidates", lambda **kwargs: [])
    monkeypatch.setattr(
        "mercury.deploy.use_cases.build_deployment_snapshot",
        lambda **kwargs: type(
            "S",
            (),
            {
                "import_count": 0,
                "on_server_count": 3,
                "deployment_needed": False,
                "skip_count": 3,
                "verified_backup_count": 3,
                "protected_source_count": 3,
                "missing_source_count": 0,
                "block_count": 0,
                "summary_message": "Deployment not needed.",
                "candidates": (),
            },
        )(),
    )
    monkeypatch.setattr(
        "mercury.deploy.use_cases.build_repo_deploy_plan",
        lambda **kwargs: type("P", (), {"planned_commands": [], "blockers": []})(),
    )
    cases = detect_deploy_use_cases()
    assert any(case.case_id == "databases_already_deployed" for case in cases)
    assert not any(case.case_id == "deploy_databases_only" for case in cases)
