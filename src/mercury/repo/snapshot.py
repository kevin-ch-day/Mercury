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


def _file_version(info):
    # Reading on a relatime filesystem can update atime. Only content/identity
    # and permission metadata belong in the change detector.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)



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
    file_records: dict[str, dict] = {}
    links: dict[str, str] = {}
    observed = {}
    total_bytes = 0
    ignored_result = subprocess.run(
        ['git', '--no-optional-locks', 'check-ignore', '--no-index', '-z', '--stdin'],
        cwd=source, input=(b'\0'.join(os.fsencode(p) for p in tracked + untracked) + b'\0') if tracked + untracked else b'',
        capture_output=True, check=False,
    )
    if ignored_result.returncode not in (0, 1):
        raise ValueError('Cannot verify ignore rules; refusing snapshot')
    ignored = set(os.fsdecode(p) for p in ignored_result.stdout.split(b'\0') if p)
    source = source.resolve()
    for name in tracked + untracked:
        path = source / name
        if (name in ignored or sensitive(name)
                or not path.resolve().is_relative_to(source)
                or any((source / Path(*Path(name).parts[:i])).is_symlink()
                       for i in range(1, len(Path(name).parts)))):
            excluded.append(name)
            continue
        data = b''
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or sensitive(target) or not (path.parent / target).resolve().is_relative_to(source):
                excluded.append(name)
                continue
            data = os.fsencode(target)
            mode = 0o777
            kind = 'symlink'
        elif path.exists():
            if not stat.S_ISREG(path.lstat().st_mode):
                excluded.append(name)
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
                    excluded.append(name)
                    continue
                data = handle.read()
                if _file_version(os.fstat(handle.fileno())) != _file_version(info):
                    raise ValueError('Source changed while reading snapshot')
            mode = stat.S_IMODE(info.st_mode) & 0o777
            kind = 'file'
        else:
            mode, kind = 0, 'deleted'
        total_bytes += len(data)
        if total_bytes > 256 * 1024 * 1024:
            raise ValueError('Uncommitted snapshot exceeds 256 MiB source budget')
        observed[name] = (kind, data, mode)
        if name in tracked:
            patch = git_bytes(source, 'diff', '--no-renames', '--binary', '--no-ext-diff', '--no-textconv', 'HEAD', '--', name)
            if b'\0' in data or b'GIT binary patch' in patch or sensitive(name, data) or sensitive(name, patch):
                excluded.append(name)
            else:
                safe_tracked.append(name)
                patches.append(patch)
                # Confirm patch projection matches the file we inspected.
                if kind == 'file' and path.read_bytes() != data:
                    raise ValueError('Tracked source changed during patch construction')
        elif b'\0' in data or sensitive(name, data):
            excluded.append(name)
        else:
            if kind == 'symlink':
                links[name] = os.fsdecode(data)
            else:
                files[name] = data
        if name not in excluded:
            file_records[name] = {'kind': kind, 'mode': mode, 'sha256': hashlib.sha256(data).hexdigest()}
    patch = b''.join(patches)
    if sensitive('', patch):
        raise ValueError('Snapshot changed during filtering; refusing to write patch')
    if dirty_paths(source) != initial or git_bytes(source, 'rev-parse', 'HEAD').decode().strip() != base_commit:
        raise ValueError('Source file list changed during snapshot; retry when stable')
    for name, (kind, data, mode) in observed.items():
        path = source / name
        if kind == 'symlink':
            stable = path.is_symlink() and os.fsencode(os.readlink(path)) == data
        elif kind == 'deleted':
            stable = not path.exists() and not path.is_symlink()
        else:
            stable = not path.is_symlink() and path.is_file() and path.read_bytes() == data and stat.S_IMODE(path.stat().st_mode) & 0o777 == mode
        if not stable:
            raise ValueError('Source bytes/mode changed during snapshot; retry when stable')
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    (destination / 'tracked.patch').write_bytes(patch)
    with tarfile.open(destination / 'untracked.tar.gz', 'w:gz') as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = file_records[name]['mode']
            archive.addfile(info, io.BytesIO(data))
        for name, target in sorted(links.items()):
            info = tarfile.TarInfo(name); info.type = tarfile.SYMTYPE; info.linkname = target
            archive.addfile(info)
    with tarfile.open(destination / 'untracked.tar.gz', 'r:gz') as archive:
        if {m.name for m in archive.getmembers()} != set(files) | set(links):
            raise ValueError('Snapshot archive membership verification failed')
        for member in archive:
            if member.issym():
                if member.linkname != links[member.name]:
                    raise ValueError('Snapshot symlink verification failed')
            elif archive.extractfile(member).read() != files[member.name]:
                raise ValueError('Snapshot archive verification failed')
    record = {'base_commit': base_commit,
              'branch': git_bytes(source, 'rev-parse', '--abbrev-ref', 'HEAD').decode().strip(),
              'format': 'mercury-worktree-snapshot-v2', 'files': file_records,
              'scope': 'HEAD-to-worktree content; staging state and ignored files not reconstructed',
              'tracked_files': safe_tracked, 'untracked_files': sorted(set(files) | set(links)),
              'excluded_files': sorted(set(excluded)), 'sha256': {}}
    for name in ('tracked.patch', 'untracked.tar.gz'):
        record['sha256'][name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
    (destination / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in record['sha256'].items()))
    (destination / 'snapshot.json').write_text(json.dumps(record, indent=2) + '\n')
    for path in destination.iterdir(): path.chmod(0o600)
    return {'path': str(destination), 'base_commit': base_commit,
            'excluded_count': len(record['excluded_files']),
            'manifest_sha256': hashlib.sha256((destination / 'snapshot.json').read_bytes()).hexdigest(),
            'saved_files': len(safe_tracked) + len(files) + len(links), 'sha256': record['sha256']}
