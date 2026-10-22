"""Fail-closed entry point for the user timer; never triggers an autofs mount."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat


def require_disk(mount: str, uuid: str, *, mountinfo: str | None = None, device_stat=None) -> None:
    """Inspect kernel metadata before any access to the mountpoint itself."""
    text = Path('/proc/self/mountinfo').read_text() if mountinfo is None else mountinfo
    expected = os.path.normpath(mount)
    actual = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        fields = left.split()
        if not sep or len(fields) < 6:
            continue
        target = fields[4].replace('\\040', ' ').replace('\\134', '\\')
        fs = right.split()[0]
        if target == expected and fs != 'autofs' and fields[2].split(':')[0] != '0':
            actual.append(fields[2])
    if len(actual) != 1:
        raise ValueError('Mercury repository sync refused: real backup disk is not mounted (autofs alone is insufficient). No backup writes attempted.')
    device = (device_stat or os.stat)('/dev/disk/by-uuid/' + uuid)
    if not stat.S_ISBLK(device.st_mode) or actual[0] != f'{os.major(device.st_rdev)}:{os.minor(device.st_rdev)}':
        raise ValueError('Mercury repository sync refused: mounted disk UUID does not match. No backup writes attempted.')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mount', required=True)
    parser.add_argument('--uuid', required=True)
    args = parser.parse_args(argv)
    try:
        require_disk(args.mount, args.uuid)
        from mercury.repo.config import load_repo_definitions
        from mercury.repo.status import inspect_repositories
        from mercury.repo.offline_clone import build_offline_clone_plan, execute_offline_clone_plan, offline_clone_root
        from mercury.repo.offline_terminal import print_offline_clone_plan
        root = offline_clone_root()
        if root.resolve().parent != Path(args.mount).resolve():
            raise ValueError('Configured repository root does not match the timer mount; refusing sync')
        plan = build_offline_clone_plan(inspect_repositories(load_repo_definitions()), root=root)
        require_disk(args.mount, args.uuid)
        result = execute_offline_clone_plan(plan, snapshots=True)
        print_offline_clone_plan(result, executed=True)
        print(f'Repository sync receipt: {result.receipt_path}', flush=True)
        return 1 if any(e.error for e in result.entries) else 0
    except (OSError, ValueError) as exc:
        print(str(exc), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
