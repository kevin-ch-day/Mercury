"""Tests for Mercury USB device detection and repair hints."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.usb_device import (
    UsbDeviceProbe,
    log_directory_repair_hint,
    probe_usb_device,
    systemd_mount_unit_name,
    usb_repair_banner,
)

def test_systemd_mount_unit_name() -> None:
    assert systemd_mount_unit_name(Path("/mnt/MERCURY_DATA_USB")) == "mnt-MERCURY_DATA_USB.mount"


def test_usb_repair_banner_when_device_attached_unmounted(tmp_path: Path) -> None:
    mount = tmp_path / "MERCURY_DATA_USB"
    device = UsbDeviceProbe(
        mount_path=mount,
        device_attached=True,
        device_path=Path("/dev/sda1"),
        systemd_mount_unit="mnt-MERCURY_DATA_USB.mount",
        fstab_configured=True,
        placeholder_mount_point=True,
        quick_mount_command="sudo systemctl start mnt-MERCURY_DATA_USB.mount",
    )
    banner = usb_repair_banner(device)
    assert banner is not None
    assert "./run.sh repair-usb" in banner


def test_log_directory_repair_hint_suggests_repair_usb_when_unmounted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mnt" / "MERCURY_DATA_USB"
    log_dir = mount / "mercury_logs"

    monkeypatch.setattr("mercury.core.usb_device.resolve_usb_mount", lambda **kwargs: mount)
    monkeypatch.setattr(
        "mercury.core.usb_device.probe_usb_device",
        lambda **kwargs: UsbDeviceProbe(
            mount_path=mount,
            device_attached=True,
            device_path=Path("/dev/sda1"),
            systemd_mount_unit="mnt-MERCURY_DATA_USB.mount",
            fstab_configured=True,
            placeholder_mount_point=True,
            quick_mount_command="sudo systemctl start mnt-MERCURY_DATA_USB.mount",
        ),
    )
    monkeypatch.setattr("mercury.core.usb_device.usb_mount_is_active", lambda path, **kwargs: False)
    hint = log_directory_repair_hint(log_dir)
    assert "repair-usb" in hint


def test_log_directory_repair_hint_suggests_repair_usb_for_owner_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mnt" / "MERCURY_DATA_USB"
    log_dir = mount / "mercury_logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setattr("mercury.core.usb_device.resolve_usb_mount", lambda **kwargs: mount)
    monkeypatch.setattr("mercury.core.usb_device.usb_mount_is_active", lambda path, **kwargs: True)
    hint = log_directory_repair_hint(log_dir, permission_detail="not writable (owner: root)")
    assert "repair-usb" in hint


def test_discover_usb_target_includes_repair_banner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mercury.core.environment_status import discover_usb_target

    mount = tmp_path / "mnt" / "MERCURY_DATA_USB"
    mount.mkdir(parents=True)
    monkeypatch.setattr(
        "mercury.core.environment_status.resolve_usb_mount",
        lambda **kwargs: mount,
    )
    monkeypatch.setattr(
        "mercury.core.usb_device.probe_usb_device",
        lambda **kwargs: UsbDeviceProbe(
            mount_path=mount,
            device_attached=True,
            device_path=Path("/dev/sda1"),
            systemd_mount_unit="mnt-MERCURY_DATA_USB.mount",
            fstab_configured=True,
            placeholder_mount_point=True,
            quick_mount_command="sudo systemctl start mnt-MERCURY_DATA_USB.mount",
        ),
    )
    monkeypatch.setattr(
        "mercury.core.environment_status.usb_mount_is_active",
        lambda path, **kwargs: False,
    )
    usb = discover_usb_target(mount_path=mount)
    assert usb.device_attached is True
    assert usb.quick_mount_command is not None
    assert usb.repair_banner is not None
    assert "repair-usb" in usb.repair_banner
