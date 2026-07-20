"""Private permissions for operator-storage artifacts."""

from __future__ import annotations

import os
from pathlib import Path


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_directory(path: Path) -> Path:
    """Create or tighten a Mercury-managed artifact directory."""
    path.mkdir(parents=True, mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
    os.chmod(path, PRIVATE_DIRECTORY_MODE)
    return path


def restrict_artifact_file(path: Path) -> Path:
    """Restrict an existing regular artifact file to the operator account."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Refusing to change permissions on non-regular artifact: {path}")
    os.chmod(path, PRIVATE_FILE_MODE)
    return path
