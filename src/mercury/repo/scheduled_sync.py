"""Fail-closed user timer with bounded approved automount activation."""
from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter
from mercury.storage.verified_volume import activate_disk, require_destination, require_disk, require_headroom


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mount', required=True)
    parser.add_argument('--uuid', required=True)
    parser.add_argument('--reuse-unchanged', action='store_true', help='Reuse verified unchanged mirrors')
    args = parser.parse_args(argv)
    started = perf_counter()
    stage = "mount"
    try:
        activate_disk(args.mount, args.uuid)
        require_disk(args.mount, args.uuid)
        from mercury.repo.config import load_repo_definitions
        from mercury.repo.status import inspect_repositories
        from mercury.repo.offline_clone import build_offline_clone_plan, execute_offline_clone_plan, offline_clone_root
        from mercury.repo.offline_terminal import print_offline_clone_plan
        stage = "destination"
        root = offline_clone_root()
        require_destination(args.mount, args.uuid, root)
        require_headroom(Path(args.mount), 10 * 1024**3)
        if root.parent != Path(args.mount):
            raise ValueError('Configured repository root does not match the timer mount; refusing sync')
        stage = "plan"
        plan = build_offline_clone_plan(inspect_repositories(load_repo_definitions()), root=root)
        requested = len(plan.entries)
        if not requested:
            raise ValueError("No repositories are configured in backup scope")
        require_disk(args.mount, args.uuid)
        stage = "mirror"
        result = execute_offline_clone_plan(plan, snapshots=True, reuse_unchanged=args.reuse_unchanged,
            guard=lambda: require_destination(args.mount, args.uuid, root))
        print_offline_clone_plan(result, executed=True)
        print(f'Repository sync receipt: {result.receipt_path}', flush=True)
        synced = sum(entry.executed and not entry.error for entry in result.entries)
        reused = sum(entry.executed and entry.mirror_reused for entry in result.entries)
        complete = bool(result.receipt_path) and synced == len(result.entries) == requested
        status = "complete" if complete else "partial"
        print(
            f"Repository sync result status={status} stage={stage} "
            f"synced={synced} requested={requested} reused={reused} "
            f"elapsed_ms={(perf_counter() - started) * 1000:.1f}",
            flush=True,
        )
        return 0 if complete else 1
    except (OSError, ValueError) as exc:
        print(str(exc), flush=True)
        print(
            f"Repository sync result status=failed stage={stage} "
            f"error_type={type(exc).__name__} elapsed_ms={(perf_counter() - started) * 1000:.1f}",
            flush=True,
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
