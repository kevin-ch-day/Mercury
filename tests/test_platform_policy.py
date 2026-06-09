"""Tests for Mercury platform detection and Windows handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.platform import PlatformInfo, _parse_os_release, detect_platform
from mercury.env.probe import probe_environment


def test_parse_os_release_extracts_id_and_name(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Fedora Linux"\nID=fedora\n', encoding="utf-8")
    distro_id, distro_name = _parse_os_release(os_release)
    assert distro_id == "fedora"
    assert distro_name == "Fedora Linux"


def test_detect_platform_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mercury.core.platform.platform.system", lambda: "Windows")
    monkeypatch.setattr("mercury.core.platform.platform.release", lambda: "11")
    info = detect_platform()
    assert info.is_windows is True
    assert info.support_label == "Windows seed-only"
    assert info.allows_live_execution is False


def test_execution_policy_refuses_live_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "mercury.core.execution_policy.detect_platform",
        lambda: PlatformInfo(system="Windows", release="11"),
    )
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
    )
    assert policy.live_execution_allowed() is False
    assert "not supported on Windows" in (policy.refusal_reason() or "")


def test_execution_policy_refuses_live_non_fedora_linux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mercury.core.execution_policy.detect_platform",
        lambda: PlatformInfo(system="Linux", release="6.9", distro_id="ubuntu", distro_name="Ubuntu"),
    )
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=tmp_path,
        config_path=tmp_path / "local.toml",
    )
    assert policy.live_execution_allowed() is False
    reason = policy.refusal_reason() or ""
    assert "supported only on Fedora" in reason
    assert "Ubuntu" in reason


def test_probe_environment_exposes_platform_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.env.probe.detect_platform",
        lambda: PlatformInfo(system="Windows", release="11"),
    )
    result = probe_environment()
    assert result.platform_support == "Windows seed-only"
    assert any("Windows detected" in note for note in result.notes)
