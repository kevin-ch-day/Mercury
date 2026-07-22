"""Guards against host-local shadow writes under inactive operator mounts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mercury.core.path_permissions import check_path_permission, safe_ensure_directory
from mercury.core.usb_mount import inactive_operator_mount_blocker
from mercury.storage.report import StorageRootStatus
from mercury.core.storage_validate import MountIdentity, MountValidationCode, MountValidationResult


def test_inactive_operator_mount_blocker_when_not_mounted(tmp_path: Path) -> None:
    mount = tmp_path / "MERCURY_DATA_V2"
    mount.mkdir()
    child = mount / "mercury_logs"
    with patch("mercury.core.usb_mount.resolve_operator_mount", return_value=mount):
        with patch("mercury.core.usb_mount.storage_mount_is_active", return_value=False):
            detail = inactive_operator_mount_blocker(child)
            assert detail is not None
            assert "not active" in detail
            check = check_path_permission(child, label="log directory")
            assert check.writable is False
            assert check.needs_repair is True
            ok, message = safe_ensure_directory(child)
            assert ok is False
            assert "not active" in message
            assert not child.exists()


def test_safe_ensure_allows_when_mount_active(tmp_path: Path) -> None:
    mount = tmp_path / "MERCURY_DATA_V2"
    mount.mkdir()
    child = mount / "mercury_logs"
    with patch("mercury.core.usb_mount.resolve_operator_mount", return_value=mount):
        with patch("mercury.core.usb_mount.storage_mount_is_active", return_value=True):
            assert inactive_operator_mount_blocker(child) is None
            ok, message = safe_ensure_directory(child)
            assert ok is True
            assert message == "created"
            assert child.is_dir()


def test_one_line_read_only_policy() -> None:
    identity = MountIdentity(
        mount_path=Path("/mnt/MERCURY_DATA_USB"),
        path_exists=True,
        is_mount=True,
        mounted_uuid="e4f0",
        mounted_fstype="ext4",
        mount_options="rw",
        writable=True,
    )
    validation = MountValidationResult(
        code=MountValidationCode.OK,
        mount_path=Path("/mnt/MERCURY_DATA_USB"),
        expected_uuid="e4f0",
        expected_fstype="ext4",
        identity=identity,
    )
    root = StorageRootStatus(
        key="legacy",
        role="legacy_archive",
        label="MERCURY_DATA_USB",
        mount_path="/mnt/MERCURY_DATA_USB",
        filesystem_uuid="e4f0",
        writable_policy=False,
        validation=validation,
        is_active_writer=False,
    )
    line = root.one_line()
    assert "read-only policy" in line
    assert "writable" not in line.split("read-only")[0] or "and writable" not in line


def test_ensure_private_directory_refuses_inactive_mount(tmp_path: Path) -> None:
    import pytest

    from mercury.core.artifact_permissions import ensure_private_directory

    mount = tmp_path / "MERCURY_DATA_V2"
    mount.mkdir()
    child = mount / "mercury_backups" / "db"
    with patch("mercury.core.usb_mount.resolve_operator_mount", return_value=mount):
        with patch("mercury.core.usb_mount.storage_mount_is_active", return_value=False):
            with pytest.raises(OSError, match="inactive operator mount"):
                ensure_private_directory(child)
    assert not child.exists()


def test_progress_ledger_refuses_inactive_primary(tmp_path: Path) -> None:
    import pytest

    from mercury.storage.progress_ledger import ensure_ledger

    mount = tmp_path / "MERCURY_DATA_V2"
    mount.mkdir()
    with patch("mercury.core.usb_mount.resolve_operator_mount", return_value=mount):
        with patch("mercury.core.usb_mount.storage_mount_is_active", return_value=False):
            with pytest.raises(OSError, match="operator mount not active"):
                ensure_ledger(mount)