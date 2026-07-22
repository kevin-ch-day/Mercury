from __future__ import annotations

import json
from pathlib import Path

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, StorageRootConfig
from mercury.storage.smart_health import (
    build_smart_health_plan,
    read_smart_health_record,
    record_smart_health,
    smart_latest_path,
)


def _config(tmp_path: Path) -> StorageConfig:
    primary = tmp_path / "hdd"
    legacy = tmp_path / "usb"
    primary.mkdir()
    legacy.mkdir()
    return StorageConfig(
        primary=StorageRootConfig("primary", StorageRootRole.CANONICAL, "HDD", primary, "hdd-uuid", "ext4", True),
        legacy=StorageRootConfig(
            "legacy",
            StorageRootRole.LEGACY_ARCHIVE,
            "USB",
            legacy,
            "usb-uuid",
            "ext4",
            False,
        ),
        active_write_role=StorageWriteRole.PRIMARY,
        migration_state=MigrationState.CUTOVER_COMPLETE,
    )


def test_suggested_legacy_archive_fstab_is_read_only(tmp_path: Path) -> None:
    from mercury.storage.report import suggested_legacy_archive_fstab_line

    cfg = _config(tmp_path)
    line = suggested_legacy_archive_fstab_line(cfg)
    assert "UUID=usb-uuid" in line
    assert str(cfg.legacy.mount_path) in line
    assert ",ro," in line


def test_record_smart_health_writes_control_receipt(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)

    monkeypatch.setattr(
        "mercury.storage.smart_health.resolve_block_device_for_mount",
        lambda _mount: "/dev/sda",
    )
    monkeypatch.setattr("mercury.storage.smart_health.shutil.which", lambda _name: "/usr/sbin/smartctl")

    class _Completed:
        def __init__(self, code: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = code
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **_kwargs):
        assert cmd[0] == "sudo"
        assert "smartctl" in cmd
        if "-H" in cmd:
            return _Completed(0, "SMART overall-health self-assessment test result: PASSED\n")
        return _Completed(0, "ID# ATTRIBUTE_NAME\n")

    result = record_smart_health(config=cfg, runner=fake_run)
    assert result.success
    assert result.path.is_file()
    assert result.path == smart_latest_path(config=cfg)
    payload = json.loads(result.path.read_text(encoding="utf-8"))
    assert payload["overall_health_passed"] is True
    assert payload["block_device"] == "/dev/sda"
    assert read_smart_health_record(config=cfg)["overall_health_passed"] is True


def test_build_smart_health_plan_includes_command(tmp_path: Path, monkeypatch) -> None:
    cfg = _config(tmp_path)
    monkeypatch.setattr(
        "mercury.storage.smart_health.resolve_block_device_for_mount",
        lambda _mount: "/dev/sdb",
    )
    monkeypatch.setattr("mercury.storage.smart_health.shutil.which", lambda _name: "/usr/sbin/smartctl")
    plan = build_smart_health_plan(config=cfg)
    assert plan["block_device"] == "/dev/sdb"
    assert "sudo smartctl" in plan["command"]
    assert "/dev/sdb" in plan["command"]
