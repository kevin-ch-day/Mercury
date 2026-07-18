"""Tests for legacy → primary migration dry-run planner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mercury.core.storage_roles import MountValidationCode, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig, default_storage_config
from mercury.core.storage_roles import StorageRootRole, MigrationState
from mercury.core.storage_space import SpaceAssessment, SpacePolicy
from mercury.core.storage_validate import MountIdentity, MountValidationResult
from mercury.storage.migrate_plan import (
    PlanAction,
    build_migration_plan,
    write_migration_plan_report,
)
from tests.conftest import make_storage_mount_tree


def _ok_validation(mount: Path, *, writable: bool = True) -> MountValidationResult:
    identity = MountIdentity(
        mount_path=mount,
        path_exists=True,
        is_mount=True,
        mounted_uuid="test-uuid",
        mounted_fstype="ext4",
        mount_options="rw",
        writable=writable,
        capacity_bytes=100 * 1024**3,
        available_bytes=80 * 1024**3,
    )
    return MountValidationResult(
        code=MountValidationCode.OK,
        mount_path=mount,
        expected_uuid="test-uuid",
        expected_fstype="ext4",
        identity=identity,
        space=SpaceAssessment(
            capacity_bytes=identity.capacity_bytes or 0,
            available_bytes=identity.available_bytes or 0,
            estimated_operation_bytes=0,
            required_reserve_bytes=20 * 1024**3,
            required_available_bytes=20 * 1024**3,
            passes=True,
        ),
    )


def _config_for_mounts(primary: Path, legacy: Path) -> StorageConfig:
    base = default_storage_config()
    return StorageConfig(
        primary=StorageRootConfig(
            key="primary",
            role=StorageRootRole.CANONICAL,
            label="MERCURY_DATA_V2",
            mount_path=primary,
            filesystem_uuid="715f29a9-2671-477b-8c8d-515d190addb9",
            filesystem_type="ext4",
            writable=True,
        ),
        legacy=StorageRootConfig(
            key="legacy",
            role=StorageRootRole.TRANSITION_SOURCE,
            label="MERCURY_DATA_USB",
            mount_path=legacy,
            filesystem_uuid="e4f0c7fb-132e-4867-9c16-5e4749f5c43a",
            filesystem_type="ext4",
            writable=True,
        ),
        active_write_role=StorageWriteRole.LEGACY,
        migration_state=MigrationState.NOT_STARTED,
        space_policy=SpacePolicy(),
        source="test",
    )


def test_migration_plan_copies_missing_and_skips_identical(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    legacy = mounts["legacy"]
    primary = mounts["primary"]

    (legacy / "notes.txt").write_text("hello", encoding="utf-8")
    (legacy / "mercury_backups" / "a.sql").write_text("dump", encoding="utf-8")
    (primary / "notes.txt").write_text("hello", encoding="utf-8")  # identical size+mtime roughly
    # Force identical signatures by copying bytes then matching mtime
    import os

    src_stat = (legacy / "notes.txt").stat()
    os.utime(primary / "notes.txt", ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))

    cfg = _config_for_mounts(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        report = build_migration_plan(config=cfg)

    assert report.conflict_count == 0
    assert report.copy_file_count == 1  # a.sql only
    assert report.skip_identical_count >= 1
    assert report.ready_for_migrate_execute is True
    assert any(e.relative_path.endswith("a.sql") and e.action == PlanAction.COPY.value for e in report.entries)


def test_migration_plan_conflicts_block_ready(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    legacy = mounts["legacy"]
    primary = mounts["primary"]
    (legacy / "clash.txt").write_text("source", encoding="utf-8")
    (primary / "clash.txt").write_text("different-content-here", encoding="utf-8")
    cfg = _config_for_mounts(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        report = build_migration_plan(config=cfg)

    assert report.conflict_count == 1
    assert report.ready_for_migrate_execute is False
    assert any("conflict" in b.lower() for b in report.blockers)


def test_migration_plan_excludes_mercury_control(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    legacy = mounts["legacy"]
    primary = mounts["primary"]
    control = legacy / ".mercury_control"
    control.mkdir()
    (control / "secret.json").write_text("{}", encoding="utf-8")
    (legacy / "keep.txt").write_text("x", encoding="utf-8")
    cfg = _config_for_mounts(primary, legacy)

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        report = build_migration_plan(config=cfg)

    assert all(not e.relative_path.startswith(".mercury_control") for e in report.entries)
    assert any("excluded from migration" in w for w in report.warnings)
    assert report.copy_file_count == 1


def test_migration_plan_write_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    cfg = _config_for_mounts(mounts["primary"], mounts["legacy"])
    (mounts["legacy"] / "f.txt").write_text("1", encoding="utf-8")

    def fake_validate(**kwargs):
        return _ok_validation(Path(kwargs["mount_path"]))

    out = tmp_path / "plan.json"
    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        report = build_migration_plan(config=cfg)
        path = write_migration_plan_report(report, out)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "copy_file_count" in text
    assert '"conflict_policy": "fail"' in text


def test_migration_plan_blocked_when_primary_unready(tmp_path: Path) -> None:
    mounts = make_storage_mount_tree(tmp_path)
    cfg = _config_for_mounts(mounts["primary"], mounts["legacy"])
    (mounts["legacy"] / "f.txt").write_text("1", encoding="utf-8")

    def fake_validate(**kwargs):
        mount = Path(kwargs["mount_path"])
        if mount == mounts["primary"]:
            identity = MountIdentity(
                mount_path=mount,
                path_exists=True,
                is_mount=False,
                stale_mountpoint_entries=("leftover",),
            )
            return MountValidationResult(
                code=MountValidationCode.STALE_MOUNTPOINT_CONTENT,
                mount_path=mount,
                expected_uuid="x",
                expected_fstype="ext4",
                identity=identity,
                blocker="directory present but not mounted",
            )
        return _ok_validation(mount)

    with patch("mercury.storage.migrate_plan.validate_storage_mount", side_effect=fake_validate):
        report = build_migration_plan(config=cfg)

    assert report.ready_for_migrate_execute is False
    assert any("Primary destination not ready" in b for b in report.blockers)


def test_pre_cutover_fixture_toml(pre_cutover_storage_config: Path) -> None:
    assert pre_cutover_storage_config.exists()
    text = pre_cutover_storage_config.read_text(encoding="utf-8")
    assert 'active_write_role = "legacy"' in text
