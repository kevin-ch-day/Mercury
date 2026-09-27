"""Private permissions for operator-storage artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_directory(path: Path) -> Path:
    """Create or tighten a Mercury-managed artifact directory."""
    from mercury.core.usb_mount import inactive_operator_mount_blocker

    inactive = inactive_operator_mount_blocker(path)
    if inactive:
        raise OSError(f"Refusing to create directory under inactive operator mount ({inactive})")
    path.mkdir(parents=True, mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
    os.chmod(path, PRIVATE_DIRECTORY_MODE)
    return path


def restrict_artifact_file(path: Path) -> Path:
    """Restrict an existing regular artifact file to the operator account."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Refusing to change permissions on non-regular artifact: {path}")
    os.chmod(path, PRIVATE_FILE_MODE)
    return path


def write_private_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Atomically replace a text artifact without a world-readable creation window."""
    directory = ensure_private_directory(path.parent)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink artifact: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            # Mercury evidence may include local hardware identity. It is
            # intentionally stored in a private, operator-only artifact.
            handle.write(text)  # lgtm[py/clear-text-storage-sensitive-data]
        temporary.replace(path)
        return restrict_artifact_file(path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
