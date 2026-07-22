"""Tests for USB archive remount-ro planning and gated execution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig
from mercury.storage.archive_remount import (
    ARCHIVE_REMOUNT_RO_CONFIRMATION,
    build_archive_remount_plan,
    execute_archive_remount_ro,
)


def _config(tmp_path: Path, *, cutover: bool = True) -> StorageConfig:
    primary = tmp_path / "hdd"
    legacy = tmp_path / "usb"
    primary.mkdir()
    legacy.mkdir()
    return StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "HDD", primary, "hdd", "ext4", True),
        legacy=StorageRootConfig(
            "legacy",
            StorageRootRole.LEGACY_ARCHIVE if cutover else StorageRootRole.TRANSITION_SOURCE,
            "USB",
            legacy,
            "usb",
            "ext4",
            False,
        ),
        active_write_role=StorageWriteRole.PRIMARY if cutover else StorageWriteRole.LEGACY,
        migration_state=MigrationState.CUTOVER_COMPLETE if cutover else MigrationState.NOT_STARTED,
    )


def test_archive_remount_plan_ready_when_rw(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("mercury.storage.archive_remount._mount_mode", lambda _path: "read-write")
    plan = build_archive_remount_plan(config=config)
    assert plan.ready is True
    assert "remount,ro" in plan.remount_command
    assert str(config.legacy.mount_path) in plan.remount_command


def test_archive_remount_blocked_before_cutover(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, cutover=False)
    monkeypatch.setattr("mercury.storage.archive_remount._mount_mode", lambda _path: "read-write")
    plan = build_archive_remount_plan(config=config)
    assert plan.ready is False
    assert any("Cutover" in b for b in plan.blockers)


def test_archive_remount_execute_requires_confirmation(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("mercury.storage.archive_remount._mount_mode", lambda _path: "read-write")
    result = execute_archive_remount_ro(confirmation="nope", config=config)
    assert result.executed is False
    assert result.success is False
    assert "Confirmation" in result.message


def test_archive_remount_execute_runs_sudo_mount(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    modes = iter(["read-write", "read-only"])
    monkeypatch.setattr("mercury.storage.archive_remount._mount_mode", lambda _path: next(modes))
    captured: list[list[str]] = []

    def _run(argv, check=False, capture_output=True, text=True):
        captured.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = execute_archive_remount_ro(
        confirmation=ARCHIVE_REMOUNT_RO_CONFIRMATION,
        config=config,
        runner=_run,
    )
    assert result.success is True
    assert result.executed is True
    assert captured == [["sudo", "mount", "-o", "remount,ro", str(config.legacy.mount_path)]]
