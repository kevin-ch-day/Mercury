import os
import stat
from types import SimpleNamespace

import pytest

from mercury.repo.scheduled_sync import require_disk, main

AUTOFS = '41 46 0:114 / /mnt/MERCURY_DATA rw - autofs systemd-1 rw\n'
DISK = '780 41 8:17 / /mnt/MERCURY_DATA rw - ext4 /dev/sdb1 rw\n'


def test_autofs_only_refuses_without_accessing_device_or_mount():
    def unexpected(*args):
        pytest.fail('must refuse before touching disk')
    with pytest.raises(ValueError, match='autofs alone'):
        require_disk('/mnt/MERCURY_DATA', 'uuid', mountinfo=AUTOFS, device_stat=unexpected)


def test_absent_disk_refuses():
    with pytest.raises(ValueError, match='not mounted'):
        require_disk('/mnt/MERCURY_DATA', 'uuid', mountinfo='')


def test_real_disk_requires_matching_uuid():
    require_disk('/mnt/MERCURY_DATA', 'uuid', mountinfo=AUTOFS + DISK,
                 device_stat=lambda p: SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8,17)))
    with pytest.raises(ValueError, match='UUID'):
        require_disk('/mnt/MERCURY_DATA', 'uuid', mountinfo=AUTOFS + DISK,
                     device_stat=lambda p: SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8,18)))


def test_missing_disk_entrypoint_logs_clean_failure(monkeypatch, capsys):
    monkeypatch.setattr('mercury.repo.scheduled_sync.activate_disk', lambda *args: None)
    monkeypatch.setattr('mercury.repo.scheduled_sync.require_disk',
                        lambda *args: (_ for _ in ()).throw(ValueError('disk absent; no writes')))
    assert main(['--mount', '/mnt/MERCURY_DATA', '--uuid', 'uuid']) == 1
    output = capsys.readouterr().out
    assert 'disk absent; no writes' in output
    assert 'status=failed stage=mount' in output


def test_empty_scheduler_scope_fails_without_creating_a_generation(tmp_path, monkeypatch, capsys):
    from mercury.repo import scheduled_sync

    for name in ("activate_disk", "require_disk", "require_destination", "require_headroom"):
        monkeypatch.setattr(scheduled_sync, name, lambda *_: None)
    monkeypatch.setattr("mercury.repo.config.load_repo_definitions", list)
    mirror_root = tmp_path / "mercury_repo_clones"
    monkeypatch.setattr("mercury.repo.offline_clone.offline_clone_root", lambda: mirror_root)

    assert main(["--mount", str(tmp_path), "--uuid", "synthetic"]) == 1
    output = capsys.readouterr().out
    assert "No repositories are configured" in output
    assert "status=failed stage=plan" in output
    assert not mirror_root.exists()
