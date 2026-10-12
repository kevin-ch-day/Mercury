"""Fresh and existing Mercury repository configuration for Permission Intel."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.repo import config as repo_config


def test_fresh_setup_uses_canonical_permission_intel_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "GitHub" / "android-permission-intel"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.setattr(repo_config.Path, "home", lambda: tmp_path)

    _, definitions = repo_config.write_local_repo_config(
        path=tmp_path / "config" / "repos.toml"
    )

    assert [(item.key, item.display_name, item.path) for item in definitions] == [
        ("android_permission_intel", "Android Permission Intel", checkout)
    ]


def test_force_refresh_preserves_existing_permission_intel_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "existing-permission-intel"
    checkout.mkdir()
    destination = tmp_path / "config" / "repos.toml"
    destination.parent.mkdir()
    existing = repo_config.RepoDefinition(
        key="android_permission_intel",
        display_name="Android Permission Intel",
        path=checkout,
        default_branch="release",
        migration_scope=False,
    )
    destination.write_text(repo_config.render_repo_config([existing]))
    monkeypatch.setattr(repo_config.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(repo_config, "_home_github_candidates", list)
    monkeypatch.setattr(
        "mercury.repo.usb_metadata.enrich_repo_definitions_from_usb",
        lambda definitions: definitions,
    )

    _, definitions = repo_config.write_local_repo_config(
        path=destination, force=True
    )

    selected = [item for item in definitions if item.key == "android_permission_intel"]
    assert len(selected) == 1
    assert selected[0].path == checkout
    assert selected[0].default_branch == "release"
    assert selected[0].migration_scope is False
