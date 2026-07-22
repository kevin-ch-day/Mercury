"""Explicit development-recovery deployment remains separate from production deploy."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mercury.cli import app
from mercury.core.execution_policy import ExecutionPolicy
from mercury.deploy.safety import assert_deployment_target
from mercury.deploy.selection import resolve_deployment_candidates


def test_development_target_remains_refused_without_explicit_lane() -> None:
    with pytest.raises(Exception, match="not an approved backup-source"):
        assert_deployment_target("erebus_threat_intel_dev")


def test_configured_development_target_is_permitted_only_in_explicit_lane() -> None:
    assert_deployment_target("erebus_threat_intel_dev", allow_development_deploy=True)
    with pytest.raises(Exception, match="not an approved backup-source"):
        assert_deployment_target("random_dev", allow_development_deploy=True)


def test_development_selection_verifies_with_explicit_development_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    policy = ExecutionPolicy(
        dry_run=True,
        live_actions_enabled=False,
        backup_root=tmp_path,
        allow_unsafe_backup_root=True,
    )
    backup_dir = tmp_path / "erebus_threat_intel_dev" / "backup"
    backup_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "mercury.deploy.selection.resolve_development_backup_sources",
        lambda *, live=False: ["erebus_threat_intel_dev"],
    )
    monkeypatch.setattr("mercury.deploy.selection.resolve_backup_directory", lambda *_args, **_kwargs: backup_dir)

    seen: dict[str, bool] = {}

    class Verification:
        verified = True
        backup_id = "dev.sql.gz"

    def fake_verify(*_args, **kwargs):
        seen["allow"] = kwargs["allow_development_backup"]
        return Verification()

    monkeypatch.setattr("mercury.deploy.selection.verify_backup_artifacts", fake_verify)
    candidates = resolve_deployment_candidates(policy=policy, allow_development_deploy=True)
    assert [candidate.source_database for candidate in candidates] == ["erebus_threat_intel_dev"]
    assert seen["allow"] is True


def test_deploy_dev_execute_requires_typed_confirmation() -> None:
    result = CliRunner().invoke(app, ["deploy", "dev", "--execute"])
    assert result.exit_code != 0
    assert "DEPLOY DEV BACKUPS" in result.output
