"""Tests for [storage] config loading and env-var compatibility (C2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.core.storage_roles import (
    ENV_LEGACY_MOUNT,
    ENV_PRIMARY_MOUNT,
    ENV_USB_MOUNT,
    MigrationState,
    StorageRootRole,
    StorageWriteRole,
)
from mercury.core.storage_roots import (
    StorageIdentityDocument,
    default_storage_config,
    load_storage_config,
)
from mercury.core.usb_mount import resolve_usb_mount
from mercury.core.execution_policy import resolve_backup_root, load_execution_policy


def test_load_storage_defaults_without_storage_section(tmp_path: Path) -> None:
    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text(
        """
[mercury]
backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"
dry_run = true
live_actions_enabled = false
""",
        encoding="utf-8",
    )
    cfg = load_storage_config(local_config=cfg_path, warn_deprecated=False)
    assert cfg.active_write_role == StorageWriteRole.LEGACY
    assert cfg.migration_state == MigrationState.NOT_STARTED
    assert cfg.legacy.role == StorageRootRole.TRANSITION_SOURCE
    assert cfg.legacy.writable is True
    assert cfg.legacy.mount_path == Path("/mnt/MERCURY_DATA_USB")
    assert cfg.primary.mount_path == Path("/mnt/MERCURY_DATA_V2")
    assert cfg.cutover_complete is False


def test_load_storage_section_pre_cutover(tmp_path: Path) -> None:
    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text(
        """
[storage]
active_write_role = "legacy"
migration_state = "not_started"

[storage.primary]
role = "canonical"
label = "MERCURY_DATA_V2"
mount_path = "/mnt/MERCURY_DATA_V2"
filesystem_uuid = "715f29a9-2671-477b-8c8d-515d190addb9"
filesystem_type = "ext4"
writable = true

[storage.legacy]
role = "transition_source"
label = "MERCURY_DATA_USB"
mount_path = "/mnt/MERCURY_DATA_USB"
filesystem_uuid = "e4f0c7fb-132e-4867-9c16-5e4749f5c43a"
filesystem_type = "ext4"
writable = true

[storage.space_policy]
minimum_free_bytes = 21474836480
minimum_free_percent = 10

[mercury]
backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"
""",
        encoding="utf-8",
    )
    cfg = load_storage_config(local_config=cfg_path, warn_deprecated=False)
    assert cfg.source == "config"
    assert cfg.active_write_root.backup_root == Path("/mnt/MERCURY_DATA_USB/mercury_backups")
    assert cfg.space_policy.minimum_free_percent == 10.0
    assert cfg.derived_paths()["backup_root"].endswith("/mercury_backups")


def test_cutover_complete_boolean_derives_primary_active(tmp_path: Path) -> None:
    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text(
        """
[storage]
cutover_complete = true
active_write_role = "legacy"
migration_state = "not_started"

[storage.primary]
mount_path = "/mnt/MERCURY_DATA_V2"
filesystem_uuid = "715f29a9-2671-477b-8c8d-515d190addb9"

[storage.legacy]
mount_path = "/mnt/MERCURY_DATA_USB"
filesystem_uuid = "e4f0c7fb-132e-4867-9c16-5e4749f5c43a"
""",
        encoding="utf-8",
    )
    cfg = load_storage_config(local_config=cfg_path, warn_deprecated=False)
    assert cfg.migration_state == MigrationState.CUTOVER_COMPLETE
    assert cfg.active_write_role == StorageWriteRole.PRIMARY
    assert cfg.legacy.role == StorageRootRole.LEGACY_ARCHIVE
    assert cfg.legacy.writable is False


def test_mercury_usb_mount_only_affects_legacy_before_cutover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text("[storage]\nactive_write_role = \"legacy\"\n", encoding="utf-8")
    monkeypatch.setenv(ENV_USB_MOUNT, "/mnt/custom_usb")
    monkeypatch.delenv(ENV_LEGACY_MOUNT, raising=False)
    monkeypatch.delenv(ENV_PRIMARY_MOUNT, raising=False)
    cfg = load_storage_config(local_config=cfg_path, warn_deprecated=False)
    assert cfg.legacy.mount_path == Path("/mnt/custom_usb").resolve()
    assert cfg.primary.mount_path == Path("/mnt/MERCURY_DATA_V2")
    assert cfg.usb_mount_deprecated_override is True


def test_mercury_usb_mount_does_not_override_primary_after_cutover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text(
        """
[storage]
active_write_role = "primary"
migration_state = "cutover_complete"

[storage.primary]
mount_path = "/mnt/MERCURY_DATA_V2"
filesystem_uuid = "715f29a9-2671-477b-8c8d-515d190addb9"

[storage.legacy]
role = "legacy_archive"
writable = false
mount_path = "/mnt/MERCURY_DATA_USB"
filesystem_uuid = "e4f0c7fb-132e-4867-9c16-5e4749f5c43a"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_USB_MOUNT, "/mnt/should_not_become_primary")
    monkeypatch.delenv(ENV_PRIMARY_MOUNT, raising=False)
    cfg = load_storage_config(local_config=cfg_path, warn_deprecated=False)
    assert cfg.active_write_role == StorageWriteRole.PRIMARY
    assert cfg.primary.mount_path == Path("/mnt/MERCURY_DATA_V2")
    assert cfg.legacy.mount_path == Path("/mnt/MERCURY_DATA_USB")


def test_role_specific_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text("[storage]\n", encoding="utf-8")
    monkeypatch.setenv(ENV_PRIMARY_MOUNT, str(tmp_path / "primary"))
    monkeypatch.setenv(ENV_LEGACY_MOUNT, str(tmp_path / "legacy"))
    cfg = load_storage_config(local_config=cfg_path, warn_deprecated=False)
    assert cfg.primary.mount_path == (tmp_path / "primary").resolve()
    assert cfg.legacy.mount_path == (tmp_path / "legacy").resolve()


def test_existing_usb_mount_and_backup_root_unchanged_by_storage_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C2 must not change resolve_usb_mount / resolve_backup_root behavior."""
    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text(
        """
[mercury]
usb_mount = "/mnt/MERCURY_DATA_USB"
backup_root = "/mnt/MERCURY_DATA_USB/mercury_backups"
dry_run = true
live_actions_enabled = false
""",
        encoding="utf-8",
    )
    monkeypatch.delenv(ENV_USB_MOUNT, raising=False)
    monkeypatch.delenv(ENV_PRIMARY_MOUNT, raising=False)
    monkeypatch.delenv(ENV_LEGACY_MOUNT, raising=False)
    monkeypatch.delenv("MERCURY_BACKUP_ROOT", raising=False)

    assert resolve_usb_mount(local_config=cfg_path) == Path("/mnt/MERCURY_DATA_USB").resolve()
    assert resolve_backup_root(local_config=cfg_path) == Path(
        "/mnt/MERCURY_DATA_USB/mercury_backups"
    ).resolve()
    policy = load_execution_policy(local_config=cfg_path)
    assert policy.usb_mount == Path("/mnt/MERCURY_DATA_USB").resolve()
    assert policy.backup_root == Path("/mnt/MERCURY_DATA_USB/mercury_backups").resolve()

    # New storage config is available alongside, still pointing writers at legacy.
    storage = load_storage_config(local_config=cfg_path, warn_deprecated=False)
    assert storage.active_write_role == StorageWriteRole.LEGACY
    assert storage.active_write_root.mount_path == Path("/mnt/MERCURY_DATA_USB")


def test_storage_identity_schema_roundtrip() -> None:
    doc = StorageIdentityDocument(
        storage_role="canonical",
        filesystem_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        filesystem_label="MERCURY_DATA_V2",
        mount_path="/mnt/MERCURY_DATA_V2",
        filesystem_type="ext4",
        device_model="WDC W",
        device_serial="WD-WX5",
        initialization_timestamp="2026-07-12T00:00:00+00:00",
        mercury_version="0.0.0",
    )
    payload = doc.model_dump()
    restored = StorageIdentityDocument.model_validate(payload)
    assert restored.filesystem_uuid == doc.filesystem_uuid
    assert restored.schema_version == default_storage_config().schema_version
