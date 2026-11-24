from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

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


@pytest.mark.parametrize('health_code,attrs_code,health_text,attrs_text,success', [
    (0, 0, 'SMART result: PASSED', 'WHEN_FAILED RAW_VALUE', True),
    (2, 0, 'SMART result: PASSED', '', False),
    (0, 4, 'SMART result: PASSED', '', False),
    (8, 0, 'SMART result: PASSED', '', False),
    (64, 0, 'SMART result: PASSED', '', False),
    (0, 0, 'health unavailable', 'PASSED', False),
    (0, 0, 'SMART result: FAILED', '', False),
])
def test_smart_command_evidence_controls_success(tmp_path, monkeypatch, health_code, attrs_code, health_text, attrs_text, success):
    cfg = _config(tmp_path)
    monkeypatch.setattr('mercury.storage.smart_health.resolve_block_device_for_mount', lambda _: '/dev/sdb')
    monkeypatch.setattr('mercury.storage.smart_health.shutil.which', lambda _: '/usr/sbin/smartctl')
    def run(argv, **kwargs):
        assert '-n' in argv and kwargs['timeout'] == 15
        return SimpleNamespace(returncode=health_code if '-H' in argv else attrs_code,
            stdout=health_text if '-H' in argv else attrs_text, stderr='')
    result = record_smart_health(config=cfg, runner=run)
    assert result.success is success
    if (health_code | attrs_code) & 7:
        assert not result.path.exists()
        assert result.payload['capture_complete'] is False


def test_smart_timeout_preserves_prior_record(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setattr('mercury.storage.smart_health.resolve_block_device_for_mount', lambda _: '/dev/sdb')
    monkeypatch.setattr('mercury.storage.smart_health.shutil.which', lambda _: '/usr/sbin/smartctl')
    path = smart_latest_path(config=cfg); path.parent.mkdir(parents=True)
    original = b'{"historical":"retained"}\n'; path.write_bytes(original)
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    result = record_smart_health(config=cfg, runner=run)
    assert not result.success and result.payload['capture_state'] == 'unavailable'
    assert path.read_bytes() == original
