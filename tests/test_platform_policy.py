"""Tests for Mercury platform detection and Windows handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.execution_policy import ExecutionPolicy
from mercury.core.platform import PlatformInfo, _parse_os_release, detect_platform
from mercury.core.usb_mount import mercury_layout_present, resolve_usb_mount
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
    assert info.support_label == "Windows supported"
    assert info.allows_live_execution is True


def test_execution_policy_allows_windows_with_usb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.core.execution_policy.detect_platform",
        lambda: PlatformInfo(system="Windows", release="11"),
    )
    monkeypatch.setattr("mercury.core.execution_policy.usb_mount_is_active", lambda path, **kwargs: True)
    usb_mount = tmp_path / "E" / "MERCURY_DATA_USB"
    backup_root = usb_mount / "mercury_backups"
    backup_root.mkdir(parents=True)
    policy = ExecutionPolicy(
        dry_run=False,
        live_actions_enabled=True,
        backup_root=backup_root,
        config_path=tmp_path / "local.toml",
        usb_mount=usb_mount,
        allow_unsafe_backup_root=False,
    )
    monkeypatch.setattr(
        "mercury.core.execution_policy._disk_usage",
        lambda path: type("usage", (), {"free": 50 * 1024 * 1024 * 1024})(),
    )
    assert policy.backup_execution_allowed() is True
    assert policy.live_execution_allowed() is True


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
    assert "Fedora and Windows" in reason
    assert "Ubuntu" in reason


def test_probe_environment_exposes_platform_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.env.probe.detect_platform",
        lambda: PlatformInfo(system="Windows", release="11"),
    )
    result = probe_environment()
    assert result.platform_support == "Windows supported"
    assert any("Windows detected" in note for note in result.notes)


def test_resolve_usb_mount_prefers_config(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        '[mercury]\nusb_mount = "E:/MERCURY_DATA_USB"\n',
        encoding="utf-8",
    )
    assert resolve_usb_mount(local_config=config_path) == Path("E:/MERCURY_DATA_USB").resolve()


def test_mercury_layout_present_requires_markers(tmp_path: Path) -> None:
    root = tmp_path / "usb"
    root.mkdir()
    (root / "mercury_backups").mkdir()
    assert mercury_layout_present(root) is False
    (root / "mercury_logs").mkdir()
    assert mercury_layout_present(root) is True
