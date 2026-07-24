"""Read-only boundary used by future package creation to accept verified captures."""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import verify_manifest


def validate_erebus_capture_for_package(root: Path, *, capture_id: str, commit: str, tree: str) -> list[str]:
    if "latest" in capture_id.lower():
        return ["unqualified latest is forbidden"]
    capture = root / "validation" / "erebus" / capture_id
    summary_path = capture / "capture_summary.json"
    reconstruction = capture / "reconstruction" / "reconstructed_identity.json"
    recovery = capture / "artifacts" / "source_recovery" / "maintenance_source_recovery.json"
    if not all(path.is_file() for path in (summary_path, reconstruction, recovery)):
        return ["verified capture evidence is incomplete"]
    try:
        summary = json.loads(summary_path.read_text())
        reconstructed = json.loads(reconstruction.read_text())
    except json.JSONDecodeError:
        return ["capture metadata is malformed"]
    errors = []
    if summary.get("status") != "CAPTURE_VERIFIED": errors.append("capture is not verified")
    if summary.get("commit") != commit or summary.get("tree") != tree: errors.append("capture identity mismatch")
    if not reconstructed.get("head_match") or not reconstructed.get("tree_match"): errors.append("reconstruction did not pass")
    if not verify_manifest(capture): errors.append("capture manifest does not verify")
    return errors
