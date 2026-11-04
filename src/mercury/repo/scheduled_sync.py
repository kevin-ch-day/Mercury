"""Fail-closed user timer with bounded approved automount activation."""
from __future__ import annotations

import argparse
from pathlib import Path
from mercury.storage.verified_volume import activate_disk, exclusive_lock, require_destination, require_disk, require_headroom


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mount', required=True)
    parser.add_argument('--uuid', required=True)
    args = parser.parse_args(argv)
    try:
        activate_disk(args.mount, args.uuid)
        require_disk(args.mount, args.uuid)
        from mercury.repo.config import load_repo_definitions
        from mercury.repo.status import inspect_repositories
        from mercury.repo.offline_clone import build_offline_clone_plan, execute_offline_clone_plan, offline_clone_root
        from mercury.repo.offline_terminal import print_offline_clone_plan
        root = offline_clone_root()
        require_destination(args.mount, args.uuid, root)
        require_headroom(Path(args.mount), 10 * 1024**3)
        if root.parent != Path(args.mount):
            raise ValueError('Configured repository root does not match the timer mount; refusing sync')
        with exclusive_lock(Path(args.mount) / '.mercury-repo-sync.lock'):
            plan = build_offline_clone_plan(inspect_repositories(load_repo_definitions()), root=root)
            require_disk(args.mount, args.uuid)
            result = execute_offline_clone_plan(plan, snapshots=True,
                guard=lambda: require_destination(args.mount, args.uuid, root))
        print_offline_clone_plan(result, executed=True)
        print(f'Repository sync receipt: {result.receipt_path}', flush=True)
        return 1 if any(e.error for e in result.entries) else 0
    except (OSError, ValueError) as exc:
        print(str(exc), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
