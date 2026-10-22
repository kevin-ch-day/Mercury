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
    monkeypatch.setattr('mercury.repo.scheduled_sync.require_disk',
                        lambda *args: (_ for _ in ()).throw(ValueError('disk absent; no writes')))
    assert main(['--mount', '/mnt/MERCURY_DATA', '--uuid', 'uuid']) == 1
    assert 'disk absent; no writes' in capsys.readouterr().out
