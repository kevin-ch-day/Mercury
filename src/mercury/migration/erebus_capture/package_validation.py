"""Read-only package-authority boundary for governed Erebus captures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .contract import validate_members
from .manifest import sha256_file, verify_manifest
from .phase3b_validation import BACKUPS
from .scanner import scan_capture

HISTORICAL_INCOMPLETE_CAPTURE_ID = "erebus_destination_candidate_3f1bb5b_20260722T150930Z"
SUPERSESSION_REASON = (
    "prior clean capture omitted required ignored maintenance.py source module"
)


@dataclass(frozen=True)
class ErebusCapturePackageAssessment:
    """Classification of one Erebus capture for destination package authority."""

    capture_id: str
    classification: str
    commit: str = ""
    tree: str = ""
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _as_optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"capture identity {field} is not a string")
    text = value.strip()
    return text or None


def _summary_commit_tree(summary: dict[str, object]) -> tuple[str, str]:
    """Return canonical commit/tree; refuse ambiguous dual-shape identities."""
    repository = summary.get("repository")
    if repository is None:
        repository_dict: dict[str, object] = {}
    elif isinstance(repository, dict):
        repository_dict = repository
    else:
        raise ValueError("capture identity repository is malformed")

    top_commit = _as_optional_string(summary.get("commit"), field="commit")
    top_tree = _as_optional_string(summary.get("tree"), field="tree")
    legacy_commit = _as_optional_string(repository_dict.get("commit"), field="repository.commit")
    legacy_tree = _as_optional_string(repository_dict.get("tree"), field="repository.tree")

    if top_commit is not None and legacy_commit is not None and top_commit != legacy_commit:
        raise ValueError("conflicting capture identity shapes")
    if top_tree is not None and legacy_tree is not None and top_tree != legacy_tree:
        raise ValueError("conflicting capture identity shapes")

    commit = top_commit if top_commit is not None else legacy_commit
    tree = top_tree if top_tree is not None else legacy_tree
    if commit is None:
        raise ValueError("capture identity commit is missing")
    if tree is None:
        raise ValueError("capture identity tree is missing")
    return commit, tree


def read_erebus_capture_identity(root: Path, capture_id: str) -> tuple[str, str] | None:
    """Return (commit, tree) from capture_summary.json.

    Returns None when the summary file is absent. Raises ValueError when the
    summary exists but identity is malformed, incomplete, or ambiguous.
    Canonical top-level fields win when present; legacy repository.* fields are
    accepted alone for backward compatibility.
    """
    summary_path = root / "validation" / "erebus" / capture_id / "capture_summary.json"
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("capture metadata is malformed") from exc
    if not isinstance(summary, dict):
        raise ValueError("capture metadata is malformed")
    return _summary_commit_tree(summary)


def validate_erebus_capture_for_package(root: Path, *, capture_id: str, commit: str, tree: str) -> list[str]:
    if "latest" in capture_id.lower():
        return ["unqualified latest is forbidden"]
    capture = root / "validation" / "erebus" / capture_id
    summary_path = capture / "capture_summary.json"
    reconstruction = capture / "reconstruction" / "reconstructed_identity.json"
    recovery = capture / "artifacts" / "source_recovery" / "maintenance_source_recovery.json"
    intake = capture / "artifacts" / "intake_contract" / "intake_contract.json"
    manifest_receipt = capture / "manifest_receipt.json"
    phase_linkage = capture / "phase3b_linkage.json"
    supersession = capture / "supersession.json"
    required = (
        summary_path, reconstruction, recovery, intake, manifest_receipt, phase_linkage, supersession,
    )
    if not all(path.is_file() for path in required):
        return ["verified capture evidence is incomplete"]
    try:
        summary = json.loads(summary_path.read_text())
        reconstructed = json.loads(reconstruction.read_text())
        receipt = json.loads(manifest_receipt.read_text())
        recovery_data = json.loads(recovery.read_text())
        phase_data = json.loads(phase_linkage.read_text())
        supersession_data = json.loads(supersession.read_text())
        summary_commit, summary_tree = _summary_commit_tree(summary)
    except (OSError, json.JSONDecodeError, TypeError):
        return ["capture metadata is malformed"]
    except ValueError as exc:
        return [str(exc)]
    errors = []
    if summary.get("status") != "CAPTURE_VERIFIED":
        errors.append("capture is not verified")
    if summary.get("historical_only") is True or summary.get("active_authority") is False:
        errors.append("capture is not active authority")
    if summary_commit != commit or summary_tree != tree:
        errors.append("capture identity mismatch")
    if not reconstructed.get("head_match") or not reconstructed.get("tree_match"):
        errors.append("reconstruction did not pass")
    if receipt.get("classification") != "CAPTURE_VERIFIED":
        errors.append("manifest receipt is not verified")
    if receipt.get("commit") != commit or receipt.get("tree") != tree:
        errors.append("manifest identity mismatch")
    if receipt.get("maintenance_sha256") != recovery_data.get("artifact_sha256"):
        errors.append("maintenance recovery mismatch")
    intake_sha = receipt.get("intake_contract_sha256")
    if intake_sha and sha256_file(intake) != intake_sha:
        errors.append("intake contract hash mismatch")
    if phase_data.get("backup_ids") != sorted(BACKUPS):
        errors.append("Phase 3B backup identity mismatch")
    if (supersession_data.get("supersedes") != HISTORICAL_INCOMPLETE_CAPTURE_ID
            or supersession_data.get("reason") != SUPERSESSION_REASON):
        errors.append("supersession metadata mismatch")
    errors.extend(scan_capture(capture, short_sha=commit[:7]))
    if not verify_manifest(capture):
        errors.append("capture manifest does not verify")
    return errors


def assess_erebus_capture_for_package(
    root: Path,
    *,
    capture_id: str,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
) -> ErebusCapturePackageAssessment:
    """Classify a capture as package authority, historical reference, missing, or refused."""
    if not capture_id or not str(capture_id).strip():
        return ErebusCapturePackageAssessment(
            capture_id or "", "REFUSED", errors=("exact capture ID is required",),
        )
    if "latest" in capture_id.lower():
        return ErebusCapturePackageAssessment(
            capture_id, "REFUSED", errors=("unqualified latest is forbidden",),
        )
    capture = root / "validation" / "erebus" / capture_id
    if not capture.exists():
        return ErebusCapturePackageAssessment(
            capture_id, "MISSING", errors=(f"required capture missing: {capture_id}",),
        )
    summary_path = capture / "capture_summary.json"
    summary: dict[str, object] = {}
    if summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
            else:
                return ErebusCapturePackageAssessment(
                    capture_id, "REFUSED", errors=("capture metadata is malformed",),
                )
        except (OSError, json.JSONDecodeError):
            return ErebusCapturePackageAssessment(
                capture_id, "REFUSED", errors=("capture metadata is malformed",),
            )
    claims_authority = (
        summary.get("status") == "CAPTURE_VERIFIED"
        and summary.get("historical_only") is not True
        and summary.get("active_authority") is not False
    )
    if not claims_authority:
        return ErebusCapturePackageAssessment(
            capture_id,
            "HISTORICAL_REFERENCE",
            warnings=(
                f"Erebus capture {capture_id} is historical and not package-verified",
            ),
        )
    try:
        identity = read_erebus_capture_identity(root, capture_id)
    except ValueError as exc:
        return ErebusCapturePackageAssessment(
            capture_id, "REFUSED", errors=(str(exc),),
        )
    if identity is None:
        return ErebusCapturePackageAssessment(
            capture_id, "REFUSED", errors=("verified capture identity is incomplete",),
        )
    commit, tree = identity
    pin_errors: list[str] = []
    if expected_commit and expected_commit != commit:
        pin_errors.append(
            f"Erebus commit differs from capture summary: arg={expected_commit} file={commit}"
        )
    if expected_tree and expected_tree != tree:
        pin_errors.append(
            f"Erebus tree differs from capture summary: arg={expected_tree} file={tree}"
        )
    if pin_errors:
        return ErebusCapturePackageAssessment(
            capture_id, "REFUSED", commit=commit, tree=tree, errors=tuple(pin_errors),
        )
    errors = validate_erebus_capture_for_package(
        root, capture_id=capture_id, commit=commit, tree=tree,
    )
    if errors:
        return ErebusCapturePackageAssessment(
            capture_id, "REFUSED", commit=commit, tree=tree, errors=tuple(errors),
        )
    return ErebusCapturePackageAssessment(
        capture_id, "PACKAGE_AUTHORITY", commit=commit, tree=tree,
    )
