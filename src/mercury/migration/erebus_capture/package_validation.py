"""Read-only boundary used by future package creation to accept verified captures."""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import verify_manifest
from .contract import validate_members
from .phase3b_validation import BACKUPS


def validate_erebus_capture_for_package(root: Path, *, capture_id: str, commit: str, tree: str) -> list[str]:
    if "latest" in capture_id.lower():
        return ["unqualified latest is forbidden"]
    capture = root / "validation" / "erebus" / capture_id
    summary_path = capture / "capture_summary.json"
    reconstruction = capture / "reconstruction" / "reconstructed_identity.json"
    recovery = capture / "artifacts" / "source_recovery" / "maintenance_source_recovery.json"
    manifest_receipt = capture / "manifest_receipt.json"
    phase_linkage = capture / "phase3b_linkage.json"
    supersession = capture / "supersession.json"
    if not all(path.is_file() for path in (summary_path, reconstruction, recovery, manifest_receipt, phase_linkage, supersession)):
        return ["verified capture evidence is incomplete"]
    try:
        summary = json.loads(summary_path.read_text())
        reconstructed = json.loads(reconstruction.read_text())
        receipt = json.loads(manifest_receipt.read_text())
        recovery_data = json.loads(recovery.read_text())
        phase_data = json.loads(phase_linkage.read_text())
        supersession_data = json.loads(supersession.read_text())
        members = {str(path.relative_to(capture)) for path in capture.rglob("*") if path.is_file()}
    except (OSError, json.JSONDecodeError):
        return ["capture metadata is malformed"]
    errors = []
    if summary.get("status") != "CAPTURE_VERIFIED": errors.append("capture is not verified")
    if summary.get("historical_only") is True or summary.get("active_authority") is False:
        errors.append("capture is not active authority")
    if summary.get("commit") != commit or summary.get("tree") != tree: errors.append("capture identity mismatch")
    if not reconstructed.get("head_match") or not reconstructed.get("tree_match"): errors.append("reconstruction did not pass")
    if receipt.get("classification") != "CAPTURE_VERIFIED": errors.append("manifest receipt is not verified")
    if receipt.get("commit") != commit or receipt.get("tree") != tree: errors.append("manifest identity mismatch")
    if receipt.get("maintenance_sha256") != recovery_data.get("artifact_sha256"): errors.append("maintenance recovery mismatch")
    if phase_data.get("backup_ids") != sorted(BACKUPS): errors.append("Phase 3B backup identity mismatch")
    if supersession_data.get("supersedes") != "erebus_destination_candidate_3f1bb5b_20260722T150930Z" or supersession_data.get("reason") != "prior clean capture omitted required ignored maintenance.py source module": errors.append("supersession metadata mismatch")
    errors.extend(validate_members(members, commit[:7]))
    if not verify_manifest(capture): errors.append("capture manifest does not verify")
    return errors
