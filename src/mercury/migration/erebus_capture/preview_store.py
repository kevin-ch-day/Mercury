"""Atomic persistence helpers for immutable preview receipt directories."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")


def validate_preview_id(value: str) -> None:
    if not _ID.fullmatch(value) or ".." in value or "/" in value or "\\" in value:
        raise ValueError("INVALID_PREVIEW_ID")


def preview_root(control_root: Path, preview_id: str) -> Path:
    validate_preview_id(preview_id)
    return control_root / "validation" / "previews" / "erebus" / preview_id


def atomic_publish(temp: Path, final: Path) -> None:
    if final.exists():
        raise ValueError("PREVIEW_ID_EXISTS")
    for path in temp.rglob("*"):
        if path.is_file():
            with path.open("rb") as handle: os.fsync(handle.fileno())
    directory_fd = os.open(temp, os.O_RDONLY); os.fsync(directory_fd); os.close(directory_fd)
    os.replace(temp, final)
    parent_fd = os.open(final.parent, os.O_RDONLY); os.fsync(parent_fd); os.close(parent_fd)


def temporary_preview_root(control_root: Path, preview_id: str) -> Path:
    final = preview_root(control_root, preview_id)
    temp = final.parent / f".{preview_id}.tmp-{uuid4().hex}"
    temp.mkdir(parents=True, mode=0o700)
    return temp
