"""Tests for environment probe."""

from mercury.env.probe import probe_environment
from mercury.core.execution_policy import load_execution_policy
from mercury.core.safety import MODE_SEED


def test_probe_returns_expected_fields() -> None:
    result = probe_environment()
    policy = load_execution_policy()
    assert result.python_version
    assert result.platform_system
    assert result.platform_support
    assert result.repo_root
    expected_mode = MODE_SEED if policy.dry_run or not policy.live_actions_enabled else "operational"
    assert result.mode == expected_mode
    assert result.dry_run_only is policy.dry_run


def test_probe_config_status_keys() -> None:
    result = probe_environment()
    assert "databases.toml" in result.config_status
    assert "local.toml" in result.config_status
    assert "platform_support" in result.config_status


def test_probe_notes_non_empty() -> None:
    result = probe_environment()
    assert len(result.notes) >= 1
