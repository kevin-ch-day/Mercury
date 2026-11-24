"""Preview-first, independent all-reference Git mirrors on operator storage."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from pydantic import BaseModel, Field

from mercury.core.artifact_permissions import ensure_private_directory
from mercury.core.usb_mount import assert_operator_storage_path, resolve_operator_mount
from mercury.repo.status import RepoStatus


OFFLINE_CLONE_DIRNAME = "mercury_repo_clones"
OFFLINE_SYNC_CONFIRMATION = "SYNC OFFLINE REPOS"
OFFLINE_SYNC_RECEIPT_NAME = "offline_repo_sync_receipt.json"
GENERATION_NAME = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")


class OfflineCloneEntry(BaseModel):
    key: str
    display_name: str
    source_path: Path
    destination_path: Path
    commit: str
    source_dirty: bool
    action: str
    error: str | None = None
    executed: bool = False
    refs: dict[str, str] = Field(default_factory=dict)
    fsck: str = "not checked"
    uncommitted_count: int = 0
    snapshot: dict | None = None
    snapshot_at: str | None = None
    snapshot_captured: bool | None = None
    mirror_reused: bool = False


class OfflineClonePlan(BaseModel):
    root: Path
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    entries: list[OfflineCloneEntry] = Field(default_factory=list)
    receipt_path: Path | None = None


def offline_clone_root() -> Path:
    """Return the HDD/operator-storage location for repository mirrors."""
    return resolve_operator_mount() / OFFLINE_CLONE_DIRNAME


def offline_sync_receipt_path(root: Path) -> Path:
    return root / OFFLINE_SYNC_RECEIPT_NAME


def load_offline_sync_receipt(root: Path) -> dict[str, object] | None:
    """Load the latest local sync evidence without failing an operator preview."""
    path = offline_sync_receipt_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _receipt_entries(receipt: dict) -> dict[str, dict]:
    rows = receipt.get("repositories")
    if not isinstance(rows, list):
        return {}
    entries = {}
    for row in rows:
        key = row.get("key") if isinstance(row, dict) else None
        if not isinstance(key, str) or not key or key in entries:
            return {}
        entries[key] = row
    return entries


def _recorded_snapshot(record: dict) -> tuple[dict | None, str | None]:
    snapshot = record.get('snapshot')
    stamp = record.get('snapshot_at')
    return (snapshot if isinstance(snapshot, dict) else None, stamp if isinstance(stamp, str) else None)


def snapshot_is_current(snapshot: object, mirror: str | Path | None, captured: object) -> bool:
    path = snapshot.get('path') if isinstance(snapshot, dict) else None
    if not isinstance(path, str) or not path:
        return False
    if captured is not None:
        return captured is True
    # Older receipts inferred current capture from the shared generation directory.
    return mirror is not None and Path(path).parent == Path(mirror).parent


def _head(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def _subprocess_error_detail(exc: subprocess.CalledProcessError, *, max_lines: int = 3) -> str:
    """Render Git failures compactly enough for an operator console row."""
    raw = (exc.stderr or exc.stdout or str(exc)).strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return str(exc)
    shown = lines[:max_lines]
    if len(lines) > max_lines:
        shown.append(f"… {len(lines) - max_lines} additional Git error line(s)")
    return " | ".join(shown)


def _destination_integrity_error(path: Path) -> str | None:
    """Return a compact fsck failure before treating an offline copy as current."""
    try:
        subprocess.run(
            ["git", "fsck", "--no-dangling"], cwd=path,
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        return _subprocess_error_detail(exc)
    return None


def _refs(path: Path) -> dict[str, str]:
    from mercury.repo.snapshot import git_bytes
    return dict(line.split(" ", 1) for line in git_bytes(
        path, "for-each-ref", "--format=%(refname) %(objectname)"
    ).decode().splitlines())


def build_offline_clone_plan(statuses: list[RepoStatus], *, root: Path | None = None) -> OfflineClonePlan:
    """Compare all refs with the last verified mirror, without writing sources."""
    from mercury.repo.snapshot import dirty_paths
    target_root = root or offline_clone_root()
    receipt = load_offline_sync_receipt(target_root) or {}
    previous = _receipt_entries(receipt)
    entries = []
    for status in statuses:
        if not status.migration_scope:
            continue
        old = previous.get(status.key, {})
        destination = target_root / status.key
        locator = old.get('mirror_path')
        if isinstance(locator, str) and locator:
            candidate = Path(locator)
            try:
                if candidate.resolve().is_relative_to(target_root.resolve()):
                    destination = candidate
            except (OSError, RuntimeError):
                pass
        snapshot, snapshot_at = _recorded_snapshot(old)
        entry = OfflineCloneEntry(key=status.key, display_name=status.display_name,
            source_path=status.path, destination_path=destination, commit=status.commit,
            source_dirty=status.dirty, action='clone', error=status.error,
            snapshot=snapshot, snapshot_at=snapshot_at)
        try:
            if status.error or not status.git_repo or status.commit == 'unknown':
                raise ValueError('Source repository is unavailable')
            entry.refs = _refs(status.path)
            tracked, untracked = dirty_paths(status.path)
            entry.uncommitted_count = len(set(tracked + untracked))
            if old.get('mirror_path') and destination.is_dir():
                error = _destination_integrity_error(destination)
                if error:
                    raise ValueError('Offline mirror integrity check failed; retained for inspection')
                entry.fsck = 'PASS'
                entry.action = ('current' if _refs(destination) == entry.refs
                                and _head(destination) == status.commit else 'update')
            elif destination.exists():
                # Keep legacy runnable copies intact; first mirror is a new generation.
                entry.action = 'update'
        except (OSError, ValueError, subprocess.CalledProcessError):
            entry.action = 'blocked'
            entry.error = entry.error or 'Repository inspection failed; source or mirror unavailable/corrupt'
        entries.append(entry)
    return OfflineClonePlan(root=target_root, entries=entries)


@contextmanager
def repository_operation_lock(root: Path):
    """Serialize Linux mirror publication and pruning on the operator volume."""
    if sys.platform == "linux":
        from mercury.storage.verified_volume import exclusive_lock

        with exclusive_lock(root.parent / '.mercury-repo-sync.lock'):
            yield
    else:
        yield


def _reuse_candidate(entry: OfflineCloneEntry, root: Path, previous: dict) -> Path | None:
    if (
        entry.action != 'current'
        or previous.get('synced') is not True
        or previous.get('refs_verified') is not True
        or previous.get('fsck') != 'PASS'
        or previous.get('commit') != entry.commit
        or previous.get('refs') != entry.refs
        or previous.get('mirror_path') != str(entry.destination_path)
    ):
        return None
    candidate = entry.destination_path
    if (
        candidate.name != f'{entry.key}.git'
        or candidate.parent.parent != root / 'generations'
        or not GENERATION_NAME.fullmatch(candidate.parent.name)
        or not candidate.is_dir()
        or candidate.resolve() != candidate.absolute()
        or candidate.stat().st_dev != root.stat().st_dev
    ):
        return None
    if any(
        path.exists() or path.is_symlink()
        for path in (candidate / 'objects/info/alternates', candidate / 'objects/info/http-alternates')
    ):
        return None
    bare = subprocess.run(
        ['git', 'rev-parse', '--is-bare-repository'], cwd=candidate,
        check=True, capture_output=True, text=True,
    )
    return candidate if bare.stdout.strip() == 'true' else None


def execute_offline_clone_plan(
    plan: OfflineClonePlan, *, snapshots: bool = False, reuse_unchanged: bool = False, guard=None,
) -> OfflineClonePlan:
    """Publish a verified receipt with independent mirrors and optional worktree snapshots."""
    if reuse_unchanged and sys.platform != 'linux':
        raise ValueError('Unchanged mirror reuse requires Linux repository locking')
    if not plan.entries:
        raise ValueError('No repositories are configured in backup scope')
    if guard:
        guard()
    assert_operator_storage_path(plan.root, action='offline repository mirror write')
    ensure_private_directory(plan.root)
    with repository_operation_lock(plan.root):
        return _execute_offline_clone_plan(plan, snapshots=snapshots, reuse_unchanged=reuse_unchanged, guard=guard)


def _execute_offline_clone_plan(plan: OfflineClonePlan, *, snapshots: bool, reuse_unchanged: bool, guard) -> OfflineClonePlan:
    import uuid
    from mercury.core.artifact_permissions import write_private_text
    from mercury.repo.snapshot import dirty_paths, snapshot_uncommitted
    from mercury.backup.durability import durable_replace_text, sync_tree, sync_directory
    if guard:
        guard()
    receipt = load_offline_sync_receipt(plan.root) or {}
    previous = _receipt_entries(receipt)
    allow_reuse = reuse_unchanged and receipt.get('status') == 'complete'
    run = plan.root / 'generations' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    ensure_private_directory(run)
    durable_replace_text(run / 'STATE.json', json.dumps({'status': 'incomplete'}) + '\n')
    for entry in plan.entries:
        old = previous.get(entry.key, {})
        entry.snapshot, entry.snapshot_at = _recorded_snapshot(old)
        entry.snapshot_captured = False
        if entry.action == 'blocked':
            continue
        try:
            if guard:
                guard()
            if not entry.key or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in entry.key):
                raise ValueError('Invalid repository key')
            before = _refs(entry.source_path)
            if before != entry.refs or _head(entry.source_path) != entry.commit:
                raise ValueError('Source refs changed after preview; retry')
            reusable = _reuse_candidate(entry, plan.root, old) if allow_reuse else None
            destination = reusable or (run / (entry.key + '.git'))
            if reusable is None:
                subprocess.run(['git', 'clone', '--mirror', '--no-hardlinks',
                                str(entry.source_path), str(destination)],
                               check=True, capture_output=True, text=True)
            # A detached HEAD is also protected by clone; verify it independently.
            if _refs(destination) != before or _head(destination) != entry.commit:
                raise ValueError('Mirror references do not match the source')
            if _destination_integrity_error(destination):
                entry.fsck = 'FAIL'
                raise ValueError('Mirror integrity check failed')
            entry.fsck = 'PASS'
            tracked, untracked = dirty_paths(entry.source_path)
            entry.uncommitted_count = len(set(tracked + untracked))
            entry.source_dirty = bool(entry.uncommitted_count)
            if snapshots and entry.source_dirty:
                captured = snapshot_uncommitted(entry.source_path, run / (entry.key + '-uncommitted'))
                if captured['base_commit'] != entry.commit:
                    raise ValueError('Snapshot commit differs from the verified mirror')
                entry.snapshot = captured
                entry.snapshot_at = datetime.now(timezone.utc).isoformat()
                entry.snapshot_captured = True
            if _refs(entry.source_path) != before or _head(entry.source_path) != entry.commit:
                raise ValueError('Source refs changed during sync; retry')
            if guard:
                guard()
            entry.destination_path = destination
            entry.mirror_reused = reusable is not None
            entry.executed = True
            entry.action = 'current'
        except (OSError, subprocess.CalledProcessError, ValueError):
            entry.action = 'blocked'
            entry.error = 'Sync or snapshot verification failed; existing backups preserved'
    complete = all(e.executed and not e.error for e in plan.entries) and bool(plan.entries)
    payload = {'receipt_version': 'offline-repo-sync-v2', 'status': 'complete' if complete else 'partial',
               'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
               'clone_root': str(plan.root), 'repositories': []}
    for entry in plan.entries:
        payload['repositories'].append({**entry.model_dump(mode='json'),
            'mirror_path': str(entry.destination_path) if entry.executed else None,
            'synced': entry.executed,
            'refs_verified': entry.executed,
            'branches': {k.removeprefix('refs/heads/'): v for k, v in entry.refs.items() if k.startswith('refs/heads/')},
            'tags': {k.removeprefix('refs/tags/'): v for k, v in entry.refs.items() if k.startswith('refs/tags/')},
            'remote_tracking': {k: v for k, v in entry.refs.items() if k.startswith('refs/remotes/')}})
    text = json.dumps(payload, indent=2, sort_keys=True) + '\n'
    plan.receipt_path = write_private_text(run / OFFLINE_SYNC_RECEIPT_NAME, text)
    if guard:
        guard()
    durable_replace_text(run / 'STATE.json', json.dumps({'status': payload['status']}) + '\n')
    sync_tree(run)
    sync_directory(run.parent)
    latest = offline_sync_receipt_path(plan.root)
    if latest.exists():
        write_private_text(run / 'previous-receipt.json', latest.read_text())
    if complete:
        if guard:
            guard()
        durable_replace_text(latest, text)
    return plan
