"""Injected side-effect boundary for synthetic and governed capture workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .storage_preflight import StorageFacts


GitRunner = Callable[[Path, tuple[str, ...]], str]
StorageResolver = Callable[[], StorageFacts]


@dataclass(frozen=True)
class CaptureContext:
    control_root: Path
    source_repo: Path
    recovery_receipt: Path
    phase3b_root: Path
    intake_contract: Path
    storage_resolver: StorageResolver
    git_runner: GitRunner | None = None
    minimum_free_bytes: int = 1
