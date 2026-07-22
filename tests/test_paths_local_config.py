"""Tests for config path resolution helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.paths import LOCAL_CONFIG, resolve_local_config


@pytest.mark.uses_operator_local_config
def test_resolve_local_config_defaults_to_repo_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MERCURY_LOCAL_CONFIG", raising=False)
    assert resolve_local_config() == LOCAL_CONFIG


def test_resolve_local_config_honors_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    override = tmp_path / "custom-local.toml"
    monkeypatch.setenv("MERCURY_LOCAL_CONFIG", str(override))
    assert resolve_local_config() == override
