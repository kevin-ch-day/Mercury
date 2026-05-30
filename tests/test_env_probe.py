"""Tests for environment probe."""

from mercury.env_probe import probe_environment
from mercury.safety import DRY_RUN_ONLY, MODE_SEED


def test_probe_returns_expected_fields() -> None:
    result = probe_environment()
    assert result.python_version
    assert result.platform_system
    assert result.repo_root
    assert result.mode == MODE_SEED
    assert result.dry_run_only is DRY_RUN_ONLY


def test_probe_config_status_keys() -> None:
    result = probe_environment()
    assert "databases.toml" in result.config_status
    assert "local.toml" in result.config_status


def test_probe_notes_non_empty() -> None:
    result = probe_environment()
    assert len(result.notes) >= 1
