from __future__ import annotations

from pathlib import Path
import stat

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig
from mercury.storage.archive_receipt import build_archive_receipt, record_archive_receipt


def _config(tmp_path: Path) -> StorageConfig:
    primary = tmp_path / "hdd"; legacy = tmp_path / "usb"
    primary.mkdir(); legacy.mkdir()
    return StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "HDD", primary, "hdd", "ext4", True),
        legacy=StorageRootConfig("legacy", StorageRootRole.LEGACY_ARCHIVE, "USB", legacy, "usb", "ext4", False),
        active_write_role=StorageWriteRole.PRIMARY, migration_state=MigrationState.CUTOVER_COMPLETE,
    )


def test_archive_receipt_preview_does_not_write(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (config.legacy.mount_path / "mercury_manifests").mkdir()
    (config.legacy.mount_path / "mercury_manifests" / "x.json").write_text("x")
    result = build_archive_receipt(config=config)
    assert result.executed is False
    assert not result.path.exists()
    assert result.payload["application_policy"] == "archive-only"


def test_archive_receipt_is_immutable_and_lists_durable_paths(tmp_path: Path) -> None:
    config = _config(tmp_path)
    target = config.legacy.mount_path / "mercury_manifests" / "x.json"
    target.parent.mkdir(); target.write_text("x")
    receipt = record_archive_receipt(config=config)
    assert receipt.path.exists()
    assert stat.S_IMODE(receipt.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt.path.parent.stat().st_mode) == 0o700
    assert any(row["path"] == "mercury_manifests/x.json" for row in receipt.payload["relative_path_manifest"])
    try:
        record_archive_receipt(config=config)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected immutable receipt refusal")


def test_archive_receipt_refuses_symlink_target(tmp_path: Path) -> None:
    config = _config(tmp_path)
    control = config.primary.control_dir
    control.mkdir()
    target = tmp_path / "outside.json"
    target.write_text("outside", encoding="utf-8")
    (control / "usb_archive_receipt.json").symlink_to(target)

    try:
        record_archive_receipt(config=config)
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("expected symlink refusal")
    assert target.read_text(encoding="utf-8") == "outside"
