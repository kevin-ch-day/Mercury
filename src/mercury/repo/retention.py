"""Repository-generation retention. Preview only unless explicitly enabled."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from mercury.repo.offline_clone import (
    GENERATION_NAME, OFFLINE_SYNC_RECEIPT_NAME, _destination_integrity_error, repository_operation_lock,
)


def _read_receipt(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('receipt is not a regular file')
    with path.open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('receipt exceeds limit')
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get('repositories'), list) or not data['repositories']:
        raise ValueError('invalid receipt shape')
    for repo in data['repositories']:
        if not isinstance(repo, dict):
            raise ValueError('invalid repository record')
        mirror = repo.get('mirror_path')
        snapshot = repo.get('snapshot')
        if mirror is not None and not isinstance(mirror, str):
            raise ValueError('invalid mirror locator')
        if snapshot is not None and (
            not isinstance(snapshot, dict) or not isinstance(snapshot.get('path'), str)
        ):
            raise ValueError('invalid snapshot locator')
    return data, hashlib.sha256(raw).hexdigest()


def retention_plan(root: Path) -> dict:
    """Keep seven latest daily copies, first monthly copy, latest receipt targets.

    Unknown/incomplete generations are always retained for manual review.
    Days/months use the UTC generation names. No files are changed.
    """
    generations = root / 'generations'
    valid = {}
    keep = set()
    reasons = {}
    parsed = {}
    fingerprints = {}
    unresolved_dependencies = False
    if not generations.is_dir() or generations.is_symlink():
        return {'keep': [], 'candidates': [], 'fingerprints': {}, 'reasons': {}}
    for path in sorted(generations.iterdir()):
        if not path.is_dir() or path.is_symlink() or not GENERATION_NAME.fullmatch(path.name):
            keep.add(path.name); reasons[path.name] = 'unknown entry; manual review'
            continue
        try:
            receipt = path / OFFLINE_SYNC_RECEIPT_NAME
            data, digest = _read_receipt(receipt)
            parsed[path.name] = data
            fingerprints[path.name] = digest
            repos = data['repositories']
            if repos and all(r.get('synced') is True and r.get('fsck') == 'PASS' for r in repos):
                valid[path.name] = data
            else:
                keep.add(path.name); reasons[path.name] = 'incomplete receipt; manual review'
        except (OSError, ValueError, KeyError, TypeError):
            keep.add(path.name); reasons[path.name] = 'incomplete receipt; manual review'
            unresolved_dependencies = True
    days = {}
    months = {}
    for name in sorted(valid):
        days[name[:8]] = name
        months.setdefault(name[:6], name)
    keep.update(list(days.values())[-7:])
    keep.update(months.values())
    # The latest status receipt and retained receipts can reference older snapshots.
    try:
        latest, _ = _read_receipt(root / OFFLINE_SYNC_RECEIPT_NAME)
    except (OSError, ValueError):
        keep.update(valid); latest = {}
    if unresolved_dependencies:
        keep.update(valid)
    def referenced(data):
        for repo in data.get('repositories', []):
            paths = [repo.get('mirror_path'), (repo.get('snapshot') or {}).get('path')]
            for value in paths:
                if not value: continue
                try:
                    relative = Path(value).resolve().relative_to(generations.resolve())
                    if relative.parts: yield relative.parts[0]
                except (OSError, ValueError, RuntimeError):
                    continue
    keep.update(referenced(latest))
    while True:
        before = set(keep)
        for name in list(keep): keep.update(referenced(parsed.get(name, {})))
        if before == keep: break
    candidates = sorted(set(valid) - keep)
    return {'keep': sorted(keep), 'candidates': candidates,
            'fingerprints': fingerprints,
            'reasons': reasons}


def prune_generations(root: Path, *, execute: bool = False, enabled: bool = False) -> dict:
    if not execute: return retention_plan(root)
    if not enabled: raise ValueError('Repository pruning is disabled; preview only')
    from mercury.core.usb_mount import assert_operator_storage_path
    assert_operator_storage_path(root, action='explicit repository generation pruning')
    with repository_operation_lock(root):
        return _prune_locked(root)


def _prune_locked(root: Path) -> dict:
    plan = retention_plan(root)
    # Refuse deletion unless retained complete mirrors and snapshots still verify.
    for name in plan['keep']:
        receipt = root / 'generations' / name / OFFLINE_SYNC_RECEIPT_NAME
        if not receipt.is_file() or receipt.is_symlink(): continue
        data = json.loads(receipt.read_text())
        for repo in data.get('repositories', []):
            mirror = repo.get('mirror_path')
            if mirror and _destination_integrity_error(Path(mirror)):
                raise ValueError('Retained mirror failed verification; refusing pruning')
            snapshot = repo.get('snapshot')
            if snapshot:
                for filename, digest in snapshot['sha256'].items():
                    path = Path(snapshot['path']) / filename
                    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        raise ValueError('Retained snapshot failed verification; refusing pruning')
    if retention_plan(root) != plan:
        raise ValueError('Generation inventory changed; refresh preview')
    for name in plan['candidates']:
        path = root / 'generations' / name
        if path.is_symlink() or path.resolve().parent != (root / 'generations').resolve():
            raise ValueError('Unsafe generation path; refusing pruning')
        shutil.rmtree(path)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm', default='')
    args = parser.parse_args()
    if args.execute and args.confirm != 'PRUNE REPO GENERATIONS':
        parser.error('Execution requires --confirm "PRUNE REPO GENERATIONS"')
    from mercury.repo.offline_clone import offline_clone_root
    result = prune_generations(offline_clone_root(), execute=args.execute,
                               enabled=os.environ.get('MERCURY_REPO_PRUNE_ENABLED') == '1')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
