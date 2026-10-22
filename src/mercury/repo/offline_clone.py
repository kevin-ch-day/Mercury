"""Preview-first, independent all-reference Git mirrors on operator storage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from pydantic import BaseModel, Field

from mercury.core.artifact_permissions import ensure_private_directory
from mercury.core.usb_mount import assert_operator_storage_path, resolve_operator_mount
from mercury.repo.status import RepoStatus


OFFLINE_CLONE_DIRNAME = "mercury_repo_clones"
OFFLINE_SYNC_CONFIRMATION = "SYNC OFFLINE REPOS"
OFFLINE_SYNC_RECEIPT_NAME = "offline_repo_sync_receipt.json"


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
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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


def _is_repairable_stale_source_head(error: str) -> bool:
    """Recognize only the obsolete zero-OID remote-HEAD metadata case.

    Older local clones can retain a zero-valued ``mercury-source/HEAD`` after
    their source workstation disappeared.  It is remote-tracking metadata, not
    an object or checked-out-file corruption.  Every other fsck result remains
    an execution blocker.
    """
    return (
        "refs/remotes/mercury-source/HEAD: invalid sha1 pointer " in error
        and "0000000000000000000000000000000000000000" in error
    )


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
    previous = {r['key']: r for r in receipt.get('repositories', [])}
    entries = []
    for status in statuses:
        if not status.migration_scope:
            continue
        old = previous.get(status.key, {})
        destination = target_root / status.key
        if old.get('mirror_path'):
            candidate = Path(old['mirror_path'])
            if candidate.resolve().is_relative_to(target_root.resolve()):
                destination = candidate
        entry = OfflineCloneEntry(key=status.key, display_name=status.display_name,
            source_path=status.path, destination_path=destination, commit=status.commit,
            source_dirty=status.dirty, action='clone', error=status.error,
            snapshot=old.get('snapshot'), snapshot_at=old.get('snapshot_at'))
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


def execute_offline_clone_plan(plan: OfflineClonePlan, *, snapshots: bool = False) -> OfflineClonePlan:
    """Create immutable all-ref mirrors; old backups and source repos stay untouched."""
    import uuid
    from mercury.core.artifact_permissions import write_private_text
    from mercury.repo.snapshot import snapshot_uncommitted
    assert_operator_storage_path(plan.root, action='offline repository mirror write')
    ensure_private_directory(plan.root)
    run = plan.root / 'generations' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    ensure_private_directory(run)
    for entry in plan.entries:
        if entry.action == 'blocked':
            continue
        try:
            if not entry.key or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in entry.key):
                raise ValueError('Invalid repository key')
            before = _refs(entry.source_path)
            if before != entry.refs or _head(entry.source_path) != entry.commit:
                raise ValueError('Source refs changed after preview; retry')
            destination = run / (entry.key + '.git')
            subprocess.run(['git', 'clone', '--mirror', '--no-hardlinks',
                            str(entry.source_path), str(destination)],
                           check=True, capture_output=True, text=True)
            # A detached HEAD is also protected by clone; verify it independently.
            if _refs(destination) != before or _head(destination) != entry.commit:
                raise ValueError('Mirror references do not match the source')
            if _refs(entry.source_path) != before or _head(entry.source_path) != entry.commit:
                raise ValueError('Source refs changed during sync; retry')
            if _destination_integrity_error(destination):
                entry.fsck = 'FAIL'
                raise ValueError('Mirror integrity check failed')
            entry.fsck = 'PASS'
            entry.destination_path = destination
            if snapshots and entry.source_dirty:
                entry.snapshot = snapshot_uncommitted(entry.source_path, run / (entry.key + '-uncommitted'))
                entry.snapshot_at = datetime.now(timezone.utc).isoformat()
            entry.executed = True
            entry.action = 'current'
        except (OSError, subprocess.CalledProcessError, ValueError):
            entry.action = 'blocked'
            entry.error = 'Sync or snapshot verification failed; existing backups preserved'
    payload = {'receipt_version': 'offline-repo-sync-v2',
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
    # Preserve the earlier receipt before refreshing the latest-status pointer.
    latest = offline_sync_receipt_path(plan.root)
    if latest.exists():
        write_private_text(run / 'previous-receipt.json', latest.read_text())
    write_private_text(latest, text)
    return plan
