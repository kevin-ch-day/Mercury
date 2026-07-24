"""Read-only validation of the pinned Phase 3B evidence contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ErebusCapturePhase3BIdentity

RUN_ID = "20260722T055400Z_phase3b"
BACKUPS = {"erebus_threat_intel_prod-full-20260722_055507_238", "android_permission_intel-full-20260722_055648_287"}
REQUIRED = ("PHASE3B_REPORT.md", "phase3b_summary.json", "dumps/dump_metadata.json", "restore/source_vs_restore_comparison.json")


def validate_phase3b(root: Path, run_id: str) -> ErebusCapturePhase3BIdentity:
    if run_id != RUN_ID or not root.is_dir():
        raise ValueError("PHASE3B_MISMATCH: run root")
    paths = [root / name for name in REQUIRED]
    if not all(path.is_file() for path in paths):
        raise ValueError("PHASE3B_MISMATCH: required evidence missing")
    try:
        summary = json.loads((root / "phase3b_summary.json").read_text())
        dumps = json.loads((root / "dumps/dump_metadata.json").read_text())
        comparison = json.loads((root / "restore/source_vs_restore_comparison.json").read_text())
    except json.JSONDecodeError as exc:
        raise ValueError("PHASE3B_MISMATCH: malformed JSON") from exc
    if summary.get("run_id") != run_id:
        raise ValueError("PHASE3B_MISMATCH: run id")
    ids = set(dumps.get("backup_ids") or [])
    if ids != BACKUPS:
        raise ValueError("PHASE3B_MISMATCH: backup IDs")
    if comparison.get("zero_unexplained_differences") is not True:
        raise ValueError("PHASE3B_MISMATCH: restore comparison")
    fingerprint = hashlib.sha256("".join(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths).encode()).hexdigest()
    return ErebusCapturePhase3BIdentity(run_id, str(root) + ":" + fingerprint)
