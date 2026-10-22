"""Opt-in, filtered uncommitted snapshots; never change the source repository."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile



def git_bytes(source: Path, *args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=source,
                          input=input, capture_output=True, check=True).stdout


def dirty_paths(source: Path) -> tuple[list[str], list[str]]:
    tracked = git_bytes(source, 'diff', '--no-renames', '--name-only', '-z', 'HEAD').split(b'\0')
    untracked = git_bytes(source, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    return ([os.fsdecode(p) for p in tracked if p], [os.fsdecode(p) for p in untracked if p])


def sensitive(path: str, data: bytes = b'') -> bool:
    parts = Path(path).parts
    if any(p.lower().startswith('.env') or re.search(
        r'(?i)(credential|secret|password|token|id_rsa|id_ed25519|\.pem$|\.key$|\.p12$|\.pfx$|\.my\.cnf$)', p
    ) for p in parts):
        return True
    if path in {'config/local.toml', '.gitconfig', '.netrc', '.npmrc', '.pypirc'}:
        return True
    return bool(re.search(
        rb'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|'
        rb'(?i:(?:mysql|mariadb|postgres(?:ql)?)://[^\s:/]+:[^\s@]+@)|'
        rb'(?i:(?:password|passwd|api_key|access_token|secret_key)\s*[:=]\s*[\x22\x27][^\x22\x27\s]{4,})', data
    ))


def snapshot_uncommitted(source: Path, destination: Path) -> dict:
    """Save HEAD-to-worktree patch and safe untracked regular files, with exclusions.

    This is a worktree recovery snapshot, not a reconstruction of staging state.
    Conservative filename/content filtering may exclude non-secret source files.
    """
    tracked, untracked = dirty_paths(source)
    initial = (tracked, untracked)
    base_commit = git_bytes(source, 'rev-parse', 'HEAD').decode().strip()
    patches: list[bytes] = []
    excluded: list[str] = []
    safe_tracked: list[str] = []
    files: dict[str, bytes] = {}
    ignored_result = subprocess.run(
        ['git', '--no-optional-locks', 'check-ignore', '--no-index', '-z', '--stdin'],
        cwd=source, input=b'\0'.join(os.fsencode(p) for p in tracked + untracked) + b'\0',
        capture_output=True, check=False,
    )
    if ignored_result.returncode not in (0, 1):
        raise ValueError('Cannot verify ignore rules; refusing snapshot')
    ignored = set(os.fsdecode(p) for p in ignored_result.stdout.split(b'\0') if p)
    source = source.resolve()
    for name in tracked + untracked:
        path = source / name
        if (name in ignored or sensitive(name) or path.is_symlink()
                or not path.resolve().is_relative_to(source)
                or any((source / Path(*Path(name).parts[:i])).is_symlink()
                       for i in range(1, len(Path(name).parts)))):
            excluded.append(name)
            continue
        data = b''
        if path.exists():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    excluded.append(name)
                    continue
                data = handle.read()
        if name in tracked:
            patch = git_bytes(source, 'diff', '--no-renames', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD', '--', name)
            if b'\0' in data or b'GIT binary patch' in patch or sensitive(name, data) or sensitive(name, patch):
                excluded.append(name)
            else:
                safe_tracked.append(name)
                patches.append(patch)
        elif b'\0' in data or sensitive(name, data):
            excluded.append(name)
        else:
            files[name] = data
    patch = b''.join(patches)
    if sensitive('', patch):
        raise ValueError('Snapshot changed during filtering; refusing to write patch')
    if dirty_paths(source) != initial or git_bytes(source, 'rev-parse', 'HEAD').decode().strip() != base_commit:
        raise ValueError('Source file list changed during snapshot; retry when stable')
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    (destination / 'tracked.patch').write_bytes(patch)
    with tarfile.open(destination / 'untracked.tar.gz', 'w:gz') as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
    with tarfile.open(destination / 'untracked.tar.gz', 'r:gz') as archive:
        if {m.name for m in archive.getmembers()} != set(files):
            raise ValueError('Snapshot archive membership verification failed')
        for member in archive:
            if archive.extractfile(member).read() != files[member.name]:
                raise ValueError('Snapshot archive verification failed')
    record = {'base_commit': base_commit,
              'tracked_files': safe_tracked, 'untracked_files': sorted(files),
              'excluded_files': sorted(set(excluded)), 'sha256': {}}
    for name in ('tracked.patch', 'untracked.tar.gz'):
        record['sha256'][name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
    (destination / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in record['sha256'].items()))
    (destination / 'snapshot.json').write_text(json.dumps(record, indent=2) + '\n')
    for path in destination.iterdir(): path.chmod(0o600)
    return {'path': str(destination), 'excluded_count': len(record['excluded_files']),
            'saved_files': len(safe_tracked) + len(files), 'sha256': record['sha256']}
