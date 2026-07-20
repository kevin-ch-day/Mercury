"""Tests for observe-only storage status reporting (C3)."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_validate import MountIdentity, MountValidationCode
from mercury.storage.report import (
    StorageRootStatus,
    StorageStatusReport,
    build_storage_status_report,
    suggested_primary_fstab_line,
)
from mercury.core.storage_roots import default_storage_config, load_storage_config
from mercury.core.storage_validate import MountValidationResult


def test_suggested_fstab_line_uses_primary_uuid() -> None:
    line = suggested_primary_fstab_line(default_storage_config())
    assert "715f29a9-2671-477b-8c8d-515d190addb9" in line
    assert "/mnt/MERCURY_DATA_V2" in line
    assert "nofail" in line


def test_dashboard_line_names_usb_writer_before_cutover(
    pre_cutover_storage_config: Path,
) -> None:
    report = build_storage_status_report(local_config=pre_cutover_storage_config)
    assert report.active_write_role == StorageWriteRole.LEGACY
    assert report.migration_state == MigrationState.NOT_STARTED
    line = report.dashboard_line()
    assert "writer=legacy" in line
    assert "migration=not_started" in line
    assert "primary" in line
    assert "next=" in line


def test_warning_for_stale_primary_mountpoint(tmp_path: Path) -> None:
    primary = tmp_path / "MERCURY_DATA_V2"
    primary.mkdir()
    (primary / "leftover_link").symlink_to("/tmp/x")
    cfg = default_storage_config()
    from mercury.core.storage_roots import StorageConfig, StorageRootConfig
    from mercury.core.storage_roles import StorageRootRole

    config = StorageConfig(
        primary=StorageRootConfig(
            key="primary",
            role=StorageRootRole.CANONICAL,
            label="MERCURY_DATA_V2",
            mount_path=primary,
            filesystem_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
            filesystem_type="ext4",
            writable=True,
        ),
        legacy=cfg.legacy,
        active_write_role=StorageWriteRole.LEGACY,
        migration_state=MigrationState.NOT_STARTED,
        space_policy=cfg.space_policy,
    )
    # Build statuses manually with real validate against tmp primary
    from mercury.storage.report import _root_status

    # Monkeypatch load by constructing report directly
    primary_status = _root_status(config, key="primary")
    legacy_status = StorageRootStatus(
        key="legacy",
        role="transition_source",
        label="USB",
        mount_path=str(cfg.legacy.mount_path),
        filesystem_uuid=cfg.legacy.filesystem_uuid,
        writable_policy=True,
        validation=MountValidationResult(
            code=MountValidationCode.OK,
            mount_path=cfg.legacy.mount_path,
            expected_uuid=cfg.legacy.filesystem_uuid,
            expected_fstype="ext4",
            identity=MountIdentity(
                mount_path=cfg.legacy.mount_path,
                path_exists=True,
                is_mount=True,
                mounted_uuid=cfg.legacy.filesystem_uuid,
                mounted_fstype="ext4",
                mount_options="rw",
                writable=True,
                capacity_bytes=100 * 1024**3,
                available_bytes=80 * 1024**3,
            ),
        ),
        is_active_writer=True,
    )
    report = StorageStatusReport(config=config, primary=primary_status, legacy=legacy_status)
    warnings = report.warning_lines()
    assert any("unexpected entries" in w for w in warnings)
    assert primary_status.validation.code in {
        MountValidationCode.STALE_MOUNTPOINT_CONTENT,
        MountValidationCode.NOT_A_MOUNT,
    }


def test_post_cutover_warns_when_legacy_archive_is_physically_read_write() -> None:
    cfg = default_storage_config()
    config = replace(
        cfg,
        legacy=replace(cfg.legacy, role=StorageRootRole.LEGACY_ARCHIVE, writable=False),
        active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
    )
    validation = MountValidationResult(
        code=MountValidationCode.OK,
        mount_path=config.legacy.mount_path,
        expected_uuid=config.legacy.filesystem_uuid,
        expected_fstype="ext4",
        identity=MountIdentity(
            mount_path=config.legacy.mount_path,
            path_exists=True,
            is_mount=True,
            mounted_uuid=config.legacy.filesystem_uuid,
            mounted_fstype="ext4",
            mount_options="rw,relatime",
            writable=True,
            capacity_bytes=100,
            available_bytes=80,
        ),
    )
    legacy = StorageRootStatus(
        key="legacy", role="legacy_archive", label="USB", mount_path="/mnt/USB",
        filesystem_uuid="uuid", writable_policy=False, validation=validation,
        is_active_writer=False,
    )
    primary = replace(legacy, key="primary", role="canonical", label="HDD", is_active_writer=True)

    report = StorageStatusReport(config=config, primary=primary, legacy=legacy)

    assert legacy.physical_mount_mode == "read-write"
    assert any("physically mounted read-write" in warning for warning in report.warning_lines())


def test_print_storage_status_smoke(capsys, tmp_path: Path) -> None:
    from mercury.storage.terminal import print_storage_status

    cfg_path = tmp_path / "local.toml"
    cfg_path.write_text("[storage]\nactive_write_role = \"legacy\"\n", encoding="utf-8")
    print_storage_status(build_storage_status_report(local_config=cfg_path))
    out = capsys.readouterr().out
    assert "Mercury Storage" in out
    assert "Active write role" in out
    assert "MERCURY_DATA_V2" in out or "primary" in out.lower()
