"""Unit tests for storage role models, space policy, and mount validation (C1)."""

from __future__ import annotations

from pathlib import Path

from mercury.core.storage_roles import (
    CONTROL_DIRNAME,
    MigrationState,
    MountValidationCode,
    StorageRootRole,
    StorageWriteRole,
)
from mercury.core.storage_roots import (
    StorageConfig,
    StorageRootConfig,
    assess_routine_write_permission,
    default_storage_config,
    write_freeze_active,
)
from mercury.core.storage_space import SpacePolicy, assess_space
from mercury.core.storage_validate import (
    MountIdentity,
    list_stale_mountpoint_entries,
    validate_storage_mount,
)


def test_default_storage_keeps_legacy_as_active_writer() -> None:
    cfg = default_storage_config()
    assert cfg.active_write_role == StorageWriteRole.LEGACY
    assert cfg.migration_state == MigrationState.NOT_STARTED
    assert cfg.cutover_complete is False
    assert cfg.legacy.role == StorageRootRole.TRANSITION_SOURCE
    assert cfg.legacy.writable is True
    assert cfg.primary.role == StorageRootRole.CANONICAL
    assert cfg.active_write_root.mount_path == Path("/mnt/MERCURY_DATA_USB")
    assert CONTROL_DIRNAME == ".mercury_control"
    assert str(cfg.primary.control_dir).endswith("/.mercury_control")


def test_post_cutover_never_falls_back_to_writable_usb_when_hdd_fails(tmp_path: Path) -> None:
    """A primary writer failure must fail closed, even with a writable USB."""
    primary = tmp_path / "missing-hdd"
    usb = tmp_path / "writable-usb"; usb.mkdir()
    cfg = StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "HDD", primary, "expected-hdd", "ext4", True),
        legacy=StorageRootConfig("legacy", StorageRootRole.LEGACY_ARCHIVE, "USB", usb, "usb", "ext4", True),
        active_write_role=StorageWriteRole.PRIMARY, migration_state=MigrationState.CUTOVER_COMPLETE,
    )
    gate = assess_routine_write_permission(cfg, validate_mount=True)
    assert gate.allowed is False
    assert gate.mount_validation is not None
    assert gate.mount_validation.code == MountValidationCode.MOUNT_PATH_MISSING


def test_space_policy_uses_max_of_floor_and_percent() -> None:
    policy = SpacePolicy(minimum_free_bytes=20 * 1024**3, minimum_free_percent=10.0)
    # 100 GiB capacity → 10% = 10 GiB < 20 GiB floor → reserve 20 GiB
    small = assess_space(policy, capacity_bytes=100 * 1024**3, available_bytes=25 * 1024**3)
    assert small.required_reserve_bytes == 20 * 1024**3
    assert small.passes is True

    # 2 TiB capacity → 10% = 204.8 GiB > 20 GiB floor
    large_cap = 2 * 1024**4
    large = assess_space(policy, capacity_bytes=large_cap, available_bytes=100 * 1024**3)
    assert large.required_reserve_bytes == int(large_cap * 0.10)
    assert large.passes is False

    with_op = assess_space(
        policy,
        capacity_bytes=100 * 1024**3,
        available_bytes=30 * 1024**3,
        estimated_operation_bytes=15 * 1024**3,
    )
    assert with_op.required_available_bytes == 35 * 1024**3
    assert with_op.passes is False


def test_stale_mountpoint_entries_reported_not_deleted(tmp_path: Path) -> None:
    mount = tmp_path / "MERCURY_DATA_V2"
    mount.mkdir()
    (mount / "scytaledroid_artifacts").symlink_to("/tmp/nowhere")
    (mount / "notes.txt").write_text("stale", encoding="utf-8")
    entries = list_stale_mountpoint_entries(mount)
    assert "scytaledroid_artifacts" in entries
    assert "notes.txt" in entries
    assert (mount / "notes.txt").exists()


def test_validate_mount_path_missing(tmp_path: Path) -> None:
    result = validate_storage_mount(
        mount_path=tmp_path / "missing",
        expected_uuid="abc",
        expected_fstype="ext4",
    )
    assert result.code == MountValidationCode.MOUNT_PATH_MISSING
    assert result.ok is False


def test_validate_unmounted_directory_with_stale_content(tmp_path: Path) -> None:
    mount = tmp_path / "v2"
    mount.mkdir()
    (mount / "leftover").write_text("x", encoding="utf-8")
    result = validate_storage_mount(
        mount_path=mount,
        expected_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        expected_fstype="ext4",
    )
    assert result.code == MountValidationCode.STALE_MOUNTPOINT_CONTENT
    assert "leftover" in result.messages[0]
    assert result.ok is False


def test_validate_wrong_uuid(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    identity = MountIdentity(
        mount_path=mount,
        path_exists=True,
        is_mount=True,
        mounted_uuid="00000000-0000-0000-0000-000000000000",
        mounted_fstype="ext4",
        mount_options="rw,relatime",
        writable=True,
        capacity_bytes=100 * 1024**3,
        available_bytes=80 * 1024**3,
    )
    result = validate_storage_mount(
        mount_path=mount,
        expected_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        expected_fstype="ext4",
        identity=identity,
    )
    assert result.code == MountValidationCode.WRONG_UUID
    assert result.ok is False


def test_validate_wrong_fstype(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    identity = MountIdentity(
        mount_path=mount,
        path_exists=True,
        is_mount=True,
        mounted_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        mounted_fstype="xfs",
        mount_options="rw",
        writable=True,
        capacity_bytes=100 * 1024**3,
        available_bytes=80 * 1024**3,
    )
    result = validate_storage_mount(
        mount_path=mount,
        expected_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        expected_fstype="ext4",
        identity=identity,
    )
    assert result.code == MountValidationCode.WRONG_FSTYPE


def test_validate_read_only_mount(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    identity = MountIdentity(
        mount_path=mount,
        path_exists=True,
        is_mount=True,
        mounted_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        mounted_fstype="ext4",
        mount_options="ro,relatime",
        writable=False,
        capacity_bytes=100 * 1024**3,
        available_bytes=80 * 1024**3,
    )
    result = validate_storage_mount(
        mount_path=mount,
        expected_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        expected_fstype="ext4",
        identity=identity,
    )
    assert result.code == MountValidationCode.READ_ONLY


def test_validate_insufficient_space(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    identity = MountIdentity(
        mount_path=mount,
        path_exists=True,
        is_mount=True,
        mounted_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        mounted_fstype="ext4",
        mount_options="rw",
        writable=True,
        capacity_bytes=100 * 1024**3,
        available_bytes=5 * 1024**3,
    )
    result = validate_storage_mount(
        mount_path=mount,
        expected_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        expected_fstype="ext4",
        space_policy=SpacePolicy(),
        identity=identity,
    )
    assert result.code == MountValidationCode.INSUFFICIENT_SPACE
    assert result.space is not None
    assert result.space.passes is False


def test_validate_ok_when_identity_matches(tmp_path: Path) -> None:
    mount = tmp_path / "mnt"
    mount.mkdir()
    identity = MountIdentity(
        mount_path=mount,
        path_exists=True,
        is_mount=True,
        mounted_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        mounted_fstype="ext4",
        mount_options="rw,relatime",
        writable=True,
        capacity_bytes=900 * 1024**3,
        available_bytes=800 * 1024**3,
    )
    result = validate_storage_mount(
        mount_path=mount,
        expected_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
        expected_fstype="ext4",
        identity=identity,
    )
    assert result.ok is True
    assert result.code == MountValidationCode.OK


def _with_migration_state(cfg, state: MigrationState):
    from mercury.core.storage_roots import StorageConfig

    return StorageConfig(
        primary=cfg.primary,
        legacy=cfg.legacy,
        active_write_role=cfg.active_write_role,
        migration_state=state,
        space_policy=cfg.space_policy,
        schema_version=cfg.schema_version,
        source=cfg.source,
    )


def test_write_freeze_during_verifying_states() -> None:
    cfg = default_storage_config()
    for state in (
        MigrationState.VERIFYING,
        MigrationState.VERIFIED,
        MigrationState.VERIFIED_PENDING_CUTOVER,
    ):
        frozen = _with_migration_state(cfg, state)
        assert write_freeze_active(frozen) is True
        gate = assess_routine_write_permission(frozen, validate_mount=False)
        assert gate.allowed is False
        assert gate.code == MountValidationCode.MIGRATION_WRITE_FREEZE


def test_routine_writes_stay_on_legacy_before_cutover() -> None:
    cfg = default_storage_config()
    gate = assess_routine_write_permission(cfg, validate_mount=False)
    assert gate.allowed is True
    assert cfg.active_write_root.key == "legacy"


def test_primary_routine_write_blocked_before_cutover() -> None:
    from mercury.core.storage_roots import StorageConfig

    cfg = default_storage_config()
    early_primary = StorageConfig(
        primary=cfg.primary,
        legacy=cfg.legacy,
        active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.NOT_STARTED,
        space_policy=cfg.space_policy,
    )
    gate = assess_routine_write_permission(early_primary, validate_mount=False)
    assert gate.allowed is False
    assert gate.code == MountValidationCode.CUTOVER_INCOMPLETE
