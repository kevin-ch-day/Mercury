from __future__ import annotations

from pathlib import Path

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig
from mercury.migration.generation import (
    ARCHIVE_RECEIPT_FILE, build_active_hdd_generation, build_usb_generation,
    read_archive_receipt, read_verified_generation, record_verified_generation,
)


def _config(tmp_path: Path) -> StorageConfig:
    legacy = tmp_path / "usb"; primary = tmp_path / "hdd"
    legacy.mkdir(); primary.mkdir()
    return StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "HDD", primary, "hdd", "ext4", True),
        legacy=StorageRootConfig("legacy", StorageRootRole.TRANSITION_SOURCE, "USB", legacy, "usb", "ext4", True),
        active_write_role=StorageWriteRole.LEGACY, migration_state=MigrationState.VERIFIED,
    )


def test_durable_change_creates_new_package_generation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = config.legacy.mount_path / "mercury_manifests" / "package.json"
    path.parent.mkdir(); path.write_text("one")
    first = build_usb_generation(config=config)
    path.write_text("two")
    assert build_usb_generation(config=config).generation != first.generation


def test_ephemeral_change_does_not_create_new_package_generation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    durable = config.legacy.mount_path / "mercury_manifests" / "package.json"
    durable.parent.mkdir(); durable.write_text("one")
    first = build_usb_generation(config=config)
    log = config.legacy.mount_path / "mercury_logs" / "live.log"
    log.parent.mkdir(); log.write_text("changed")
    assert build_usb_generation(config=config).generation == first.generation


def test_matching_record_is_current_and_durable_change_stales_it(tmp_path: Path) -> None:
    config = _config(tmp_path)
    path = config.legacy.mount_path / "mercury_worktree_snapshots" / "x" / "snapshot.json"
    path.parent.mkdir(parents=True); path.write_text("first")
    current = build_usb_generation(config=config)
    record_verified_generation(current, config=config)
    assert read_verified_generation(config=config) == current.generation
    path.write_text("second")
    assert build_usb_generation(config=config).generation != read_verified_generation(config=config)


def test_post_cutover_hdd_change_does_not_rewrite_historical_usb_evidence(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = StorageConfig(
        primary=config.primary, legacy=config.legacy, active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
    )
    usb_path = config.legacy.mount_path / "mercury_manifests" / "cutover.json"
    hdd_path = config.primary.mount_path / "mercury_manifests" / "cutover.json"
    usb_path.parent.mkdir(); hdd_path.parent.mkdir()
    usb_path.write_text("archive"); hdd_path.write_text("archive")
    record_verified_generation(build_usb_generation(config=config), config=config)
    historical = read_verified_generation(config=config)
    first_active = build_active_hdd_generation(config=config)
    (config.primary.mount_path / "mercury_backups" / "new.sql.gz").parent.mkdir()
    (config.primary.mount_path / "mercury_backups" / "new.sql.gz").write_text("new backup")
    assert build_active_hdd_generation(config=config).generation != first_active.generation
    assert read_verified_generation(config=config) == historical


def test_hdd_only_bulk_store_does_not_change_active_mercury_generation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config = StorageConfig(
        primary=config.primary, legacy=config.legacy, active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
    )
    package = config.primary.mount_path / "mercury_manifests" / "current.json"
    package.parent.mkdir(); package.write_text("package")
    first = build_active_hdd_generation(config=config)
    bulk = config.primary.mount_path / "scytaledroid_artifacts" / "large.bin"
    bulk.parent.mkdir(); bulk.write_text("not mercury package")
    assert build_active_hdd_generation(config=config).generation == first.generation


def test_archive_receipt_is_read_separately_from_generation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    receipt = config.primary.control_dir / ARCHIVE_RECEIPT_FILE
    receipt.parent.mkdir(); receipt.write_text('{"final_usb_archive_generation":"fixed"}')
    assert read_archive_receipt(config=config) == {"final_usb_archive_generation": "fixed"}
