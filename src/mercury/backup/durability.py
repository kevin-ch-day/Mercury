"""Publication barriers for new, private backup generations."""

import os
from pathlib import Path


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_tree(root: Path) -> None:
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in files:
            p = base / name
            if p.is_symlink():
                continue
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        for name in dirs:
            if not (base / name).is_symlink():
                sync_directory(base / name)
        sync_directory(base)


def durable_replace_text(path: Path, text: str) -> None:
    from mercury.core.artifact_permissions import write_private_text

    write_private_text(path, text)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(path.parent)
