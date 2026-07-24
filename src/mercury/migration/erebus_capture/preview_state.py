"""Durable, fail-closed state transitions for immutable preview receipts."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path


class PreviewState(StrEnum):
    READY = "READY"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    CONSUMED = "CONSUMED"
    REFUSED = "REFUSED"
    INVALIDATED = "INVALIDATED"


def state_path(root: Path) -> Path:
    return root / "preview_state.json"


def load_state(root: Path) -> PreviewState:
    try:
        return PreviewState(json.loads(state_path(root).read_text(encoding="utf-8"))["state"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("PREVIEW_STATE_CORRUPT") from exc


def write_state(root: Path, state: PreviewState) -> None:
    target = state_path(root)
    temp = target.with_name(f".{target.name}.tmp")
    temp.write_text(json.dumps({"state": state}, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(target)


def _transition(root: Path, expected: PreviewState, target: PreviewState) -> bool:
    """Make a state transition with an exclusive per-preview lock.

    The lock is deliberately a sibling of the receipt, so two processes cannot
    both observe READY and begin the same later capture.  A stale lock fails
    closed rather than guessing whether another operation is still active.
    """
    lock = root / ".preview_state.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        os.close(descriptor)
        if load_state(root) is not expected:
            return False
        write_state(root, target)
        return True
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def begin_execution(root: Path) -> bool:
    return _transition(root, PreviewState.READY, PreviewState.EXECUTION_STARTED)


def mark_consumed(root: Path) -> bool:
    return _transition(root, PreviewState.EXECUTION_STARTED, PreviewState.CONSUMED)


def invalidate(root: Path) -> None:
    """Fail closed after detected drift or receipt corruption."""
    write_state(root, PreviewState.INVALIDATED)


def mark_refused(root: Path) -> None:
    """Record an explicit non-destructive refusal."""
    write_state(root, PreviewState.REFUSED)
