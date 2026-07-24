"""Service boundary for governed Erebus captures.

Preview is read-only. Capture execution is synthetic-only behind
``CaptureContext.allow_synthetic_execution``; production CLI and menu
contexts remain locked.
"""

from __future__ import annotations

import subprocess
import json
import hashlib
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from .models import ErebusCapturePreview, ErebusCaptureRequest, ErebusCaptureResult
from .preview_state import PreviewState, load_state, write_state
from .preview_state import invalidate
from .context import CaptureContext
from .storage_preflight import validate_storage
from .recovery_validation import validate_recovery_receipt
from .phase3b_validation import validate_phase3b
from .intake_validation import validate_intake_contract
from .preview_store import atomic_publish, preview_root, temporary_preview_root, validate_preview_id
from .contract import REQUIRED as CAPTURE_REQUIRED_MEMBERS, expected_bundle_name


GIT_TIMEOUT_SECONDS = 30
PREVIEW_REQUIRED_FILES = frozenset({
    "capture_preview.json", "capture_preview.sha256", "source_identity.json",
    "storage_identity.json", "recovery_identity.json", "phase3b_identity.json",
    "intake_identity.json", "intended_members.json", "preflight_report.json",
    "safety_decision.json", "preview_state.json",
})


@dataclass(frozen=True)
class PreviewPayload:
    """Fully validated preview content which has not been published yet."""

    request: ErebusCaptureRequest
    final_preview: Path
    final_capture: Path
    documents: dict[str, object]


def preview_receipt_files() -> frozenset[str]:
    """The complete, closed set of files permitted in a durable preview."""
    return PREVIEW_REQUIRED_FILES


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, timeout=GIT_TIMEOUT_SECONDS
    ).strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preview_manifest(root: Path) -> dict[str, str]:
    return {path.name: _sha256(path) for path in sorted(root.glob("*.json"))}


def _receipt_sha256(path: Path) -> tuple[str, str] | None:
    """Return a receipt's digest only when its sidecar verifies exactly."""
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        return None
    fields = sidecar.read_text(encoding="utf-8").split()
    if not fields or fields[0] != _sha256(path):
        return None
    return str(path), fields[0]


def _source_identity(request: ErebusCaptureRequest) -> dict[str, object]:
    """Validate and capture every pinned source fact without writing anything."""
    repo = Path(request.repository)
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError("SOURCE_MISMATCH: repository is not a Git worktree")
    try:
        branch = _git(repo, "branch", "--show-current")
        head = _git(repo, "rev-parse", "HEAD")
        tree = _git(repo, "rev-parse", "HEAD^{tree}")
        origin_main = _git(repo, "rev-parse", "origin/main")
        status = _git(repo, "status", "--porcelain")
        maintenance = repo / "src/database/db_query/virustotal_queries/reports/maintenance.py"
        relative = str(maintenance.relative_to(repo))
        tracked = _git(repo, "ls-files", "--error-unmatch", relative) == relative
        ignored = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", relative],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
        ).returncode == 0
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"SOURCE_MISMATCH: git preflight failed: {exc}") from exc
    errors = []
    if branch != "main": errors.append("branch is not main")
    if head != request.expected_commit: errors.append("HEAD differs from expected commit")
    if tree != request.expected_tree: errors.append("tree differs from expected tree")
    if origin_main != request.expected_commit: errors.append("origin/main differs from expected commit")
    if status: errors.append("repository worktree is not clean")
    if not maintenance.is_file(): errors.append("maintenance.py is missing")
    elif not tracked: errors.append("maintenance.py is not tracked")
    elif ignored: errors.append("maintenance.py is ignored")
    elif _sha256(maintenance) != request.maintenance_sha256: errors.append("maintenance.py hash differs from expected value")
    if errors:
        raise ValueError("SOURCE_MISMATCH: " + "; ".join(errors))
    return {
        "repository": str(repo), "branch": branch, "head": head, "tree": tree,
        "origin_main": origin_main, "clean": True,
        "tracked_file_count": len([line for line in _git(repo, "ls-files").splitlines() if line]),
        "submodules": _git(repo, "submodule", "status"),
        "maintenance_sha256": request.maintenance_sha256,
    }


def build_preview_payload(context: CaptureContext, request: ErebusCaptureRequest) -> PreviewPayload:
    """Validate all injected identities and build preview documents in memory.

    This function intentionally creates neither a preview nor a capture
    directory.  ``publish_preview`` is the sole final-directory writer.
    """
    validate_preview_id(request.preview_id)
    if not request.capture_id or "/" in request.capture_id or "\\" in request.capture_id or ".." in request.capture_id:
        raise ValueError("INVALID_CAPTURE_ID")
    request = ErebusCaptureRequest(**{
        **request.__dict__, "repository": str(context.source_repo),
        "control_root": str(context.control_root),
        "recovery_receipt": str(context.recovery_receipt),
        "intake_contract": str(context.intake_contract),
    })
    final_preview = preview_root(context.control_root, request.preview_id)
    final_capture = context.control_root / "validation" / "erebus" / request.capture_id
    if final_preview.exists():
        raise ValueError("PREVIEW_ID_EXISTS")
    if final_capture.exists():
        raise ValueError("FINAL_CAPTURE_EXISTS")
    source = _source_identity(request)
    storage = validate_storage(context.storage_resolver(), minimum_free_bytes=context.minimum_free_bytes)
    recovery = validate_recovery_receipt(
        context.recovery_receipt, artifact_sha256=request.maintenance_sha256,
        repair_commit=request.expected_commit, repair_tree=request.expected_tree,
    )
    phase = validate_phase3b(context.phase3b_root, request.phase3b_run_id)
    intake = validate_intake_contract(context.intake_contract)
    members = sorted(CAPTURE_REQUIRED_MEMBERS | {expected_bundle_name(request.expected_commit[:7])})
    identities = {
        "source_identity.json": source,
        "storage_identity.json": storage.__dict__,
        "recovery_identity.json": recovery.__dict__,
        "phase3b_identity.json": phase.__dict__,
        "intake_identity.json": intake.__dict__,
    }
    preview = {
        "schema": "mercury.erebus_capture_preview.v1", "preview_id": request.preview_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "capture_id": request.capture_id,
        "final_path": str(final_capture), "request": request.__dict__, "identity": source,
        "identities": identities, "intended_members": members,
        "validation_exception": [
            "tests/data_analysis/test_queue_expansion_ops.py",
            "tests/scripts/test_scripts_maintenance_inventory.py", "tests/test_setup_script.py",
        ],
    }
    documents: dict[str, object] = {
        **identities, "capture_preview.json": preview, "intended_members.json": members,
        "preflight_report.json": {"ok": True, "errors": [], "validator_order": [
            "source", "storage", "recovery", "phase3b", "intake"],},
        "safety_decision.json": {"classification": "PREVIEW_READY", "no_capture_created": True,
                                 "execute_authorized": False},
    }
    return PreviewPayload(request, final_preview, final_capture, documents)


def _write_preview_documents(root: Path, payload: PreviewPayload) -> None:
    for name, value in payload.documents.items():
        if name not in PREVIEW_REQUIRED_FILES:
            raise ValueError("PREVIEW_DOCUMENT_INVALID")
        (root / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    write_state(root, PreviewState.READY)
    manifest = _preview_manifest(root)
    (root / "capture_preview.sha256").write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")


def publish_preview(payload: PreviewPayload) -> ErebusCapturePreview:
    """Atomically publish an already-built payload and reload it for proof."""
    preview = ErebusCapturePreview(payload.request.preview_id, payload.request, True)
    root: Path | None = None
    try:
        root = temporary_preview_root(Path(payload.request.control_root), payload.request.preview_id)
        _write_preview_documents(root, payload)
        atomic_publish(root, payload.final_preview)
        root = None
        verified = load_preview(payload.request.control_root, payload.request.preview_id)
        if not verified.ok:
            raise ValueError("PREVIEW_POST_PUBLISH_VERIFY_FAILED: " + "; ".join(verified.errors))
        preview.path = str(payload.final_preview)
        return preview
    except (OSError, ValueError) as exc:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
        # An error after rename (for example parent fsync or reload failure)
        # must not leave a receipt that callers could mistake for verified.
        if payload.final_preview.exists():
            shutil.rmtree(payload.final_preview, ignore_errors=True)
        return ErebusCapturePreview(payload.request.preview_id, payload.request, False, [str(exc)], reason_codes=["PREVIEW_PERSISTENCE_FAILED"])


def preview_capture(request: ErebusCaptureRequest, *, identity_payloads: dict[str, object] | None = None) -> ErebusCapturePreview:
    """Perform no-write identity preflight for an explicit capture request."""
    repo = Path(request.repository)
    errors: list[str] = []
    if not repo.is_dir() or not (repo / ".git").exists():
        errors.append("repository is not a Git worktree")
    else:
        try:
            if _git(repo, "branch", "--show-current") != "main":
                errors.append("repository branch must be main")
            if _git(repo, "rev-parse", "HEAD") != request.expected_commit:
                errors.append("HEAD differs from expected commit")
            if _git(repo, "rev-parse", "HEAD^{tree}") != request.expected_tree:
                errors.append("tree differs from expected tree")
            if _git(repo, "rev-parse", "origin/main") != request.expected_commit:
                errors.append("origin/main differs from expected commit")
            if _git(repo, "status", "--porcelain"):
                errors.append("repository worktree is not clean")
            maintenance = repo / "src/database/db_query/virustotal_queries/reports/maintenance.py"
            if not maintenance.is_file():
                errors.append("maintenance.py is missing")
            elif _git(repo, "ls-files", "--error-unmatch", str(maintenance.relative_to(repo))):
                ignored_result = subprocess.run(
                    ["git", "-C", str(repo), "check-ignore", "-q", str(maintenance.relative_to(repo))],
                    capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
                )
                if ignored_result.returncode not in (0, 1):
                    errors.append("unable to determine whether maintenance.py is ignored")
                ignored = ignored_result.returncode == 0
                if ignored:
                    errors.append("maintenance.py is ignored")
                digest = _sha256(maintenance)
                if digest != request.maintenance_sha256:
                    errors.append("maintenance.py hash differs from expected value")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            errors.append(f"git preflight failed: {exc}")
    preview = ErebusCapturePreview(preview_id=request.preview_id, request=request, ok=not errors, errors=errors)
    if errors:
        preview.reason_codes = ["PREFLIGHT_FAILED"]
        return preview
    if not request.control_root:
        preview.ok = False
        preview.errors.append("control_root is required for durable preview")
        preview.reason_codes = ["CONTROL_ROOT_MISSING"]
        return preview
    control = Path(request.control_root)
    recovery = _receipt_sha256(Path(request.recovery_receipt)) if request.recovery_receipt else None
    if request.recovery_receipt and recovery is None:
        preview.ok = False; preview.errors.append("recovery receipt checksum is missing or invalid")
        preview.reason_codes = ["RECOVERY_RECEIPT_INVALID"]
        return preview
    intake = Path(request.intake_contract) if request.intake_contract else None
    if intake is not None and not intake.is_file():
        preview.ok = False; preview.errors.append("intake contract is missing")
        preview.reason_codes = ["INTAKE_CONTRACT_MISSING"]
        return preview
    phase = control / "phase3b" / request.phase3b_run_id
    if (control / "phase3b").exists() and not phase.is_dir():
        preview.ok = False; preview.errors.append("Phase 3B run is missing")
        preview.reason_codes = ["PHASE3B_MISSING"]
        return preview
    final_path = control / "validation" / "erebus" / request.capture_id
    if final_path.exists():
        preview.ok = False
        preview.errors.append("final capture path already exists")
        preview.reason_codes = ["FINAL_CAPTURE_EXISTS"]
        return preview
    try:
        final_preview = preview_root(control, preview.preview_id)
        if final_preview.exists():
            preview.ok = False
            preview.errors.append("preview ID already exists")
            preview.reason_codes = ["PREVIEW_ID_EXISTS"]
            return preview
        root = temporary_preview_root(control, preview.preview_id)
    except (OSError, ValueError) as exc:
        preview.ok = False
        preview.errors.append(str(exc))
        preview.reason_codes = ["PREVIEW_PERSISTENCE_FAILED"]
        return preview
    repo = Path(request.repository)
    identity = {
        "repository": str(repo), "branch": _git(repo, "branch", "--show-current"),
        "head": _git(repo, "rev-parse", "HEAD"), "tree": _git(repo, "rev-parse", "HEAD^{tree}"),
        "origin_main": _git(repo, "rev-parse", "origin/main"), "clean": True,
        "tracked_file_count": len([line for line in _git(repo, "ls-files").splitlines() if line]),
        "submodules": _git(repo, "submodule", "status"),
    }
    short_sha = request.expected_commit[:7]
    members = sorted(CAPTURE_REQUIRED_MEMBERS | {expected_bundle_name(short_sha)})
    payload = {"schema": "mercury.erebus_capture_preview.v1", "preview_id": preview.preview_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "capture_id": request.capture_id,
        "final_path": str(final_path),
        "request": request.__dict__, "identity": identity, "intended_members": members,
        "recovery_receipt": {"path": recovery[0], "sha256": recovery[1]} if recovery else None,
        "intake_contract": {"path": str(intake), "sha256": _sha256(intake)} if intake else None,
        "phase3b": {"run_id": request.phase3b_run_id, "path": str(phase)},
        "validation_exception": ["tests/data_analysis/test_queue_expansion_ops.py", "tests/scripts/test_scripts_maintenance_inventory.py", "tests/test_setup_script.py"]}
    # ``preview_capture`` is retained as the source-only lower-level helper for
    # tests and callers migrating to ``create_preview``.  It still emits a
    # structurally complete receipt; only ``create_preview`` may authorize an
    # operator preview because it replaces these placeholders with validated
    # external identities.
    if identity_payloads is None:
        identity_payloads = {
            "storage_identity.json": {"uuid": "source-only-unvalidated"},
            "recovery_identity.json": {"receipt_path": str(request.recovery_receipt), "receipt_sha256": recovery[1] if recovery else ""},
            "phase3b_identity.json": {"run_id": request.phase3b_run_id},
            "intake_identity.json": {"path": str(request.intake_contract)},
        }
    def dump(name: str, value: object) -> None:
        (root / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    dump("source_identity.json", identity)
    dump("intended_members.json", members)
    dump("preflight_report.json", {"ok": True, "errors": []})
    dump("safety_decision.json", {"classification": "PREVIEW_READY", "no_capture_created": True})
    write_state(root, PreviewState.READY)
    dump("capture_preview.json", payload)
    for filename, value in (identity_payloads or {}).items():
        if filename not in PREVIEW_REQUIRED_FILES:
            preview.ok = False
            preview.errors.append("invalid preview receipt file")
            preview.reason_codes = ["PREVIEW_PERSISTENCE_FAILED"]
            return preview
        dump(filename, value)
    manifest = _preview_manifest(root)
    (root / "capture_preview.sha256").write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    try:
        atomic_publish(root, final_preview)
    except (OSError, ValueError) as exc:
        preview.ok = False
        preview.errors.append(str(exc))
        preview.reason_codes = ["PREVIEW_PERSISTENCE_FAILED"]
        return preview
    preview.path = str(final_preview)
    return preview


def create_preview(context: CaptureContext, request: ErebusCaptureRequest) -> ErebusCapturePreview:
    """Build, atomically publish, then reload a complete governed preview."""
    try:
        payload = build_preview_payload(context, request)
    except ValueError as exc:
        code = str(exc).split(":", 1)[0]
        return ErebusCapturePreview("", request, False, [str(exc)], reason_codes=[code])
    return publish_preview(payload)


def execute_capture(context: CaptureContext, preview_id: str) -> ErebusCaptureResult:
    """Execute only in explicitly synthetic contexts; real execution is locked."""
    if not context.allow_synthetic_execution:
        return ErebusCaptureResult("EXECUTION_NOT_AUTHORIZED", False, ["Phase B is implemented but real execution requires a separate authorization."])
    checked = revalidate_preview_for_execute(context, preview_id)
    if not checked.ok:
        return checked
    root = preview_root(context.control_root, preview_id)
    if not begin_preview_execution(context.control_root, preview_id).ok:
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_NOT_READY"])
    try:
        data = json.loads((root / "capture_preview.json").read_text(encoding="utf-8"))
        request = ErebusCaptureRequest(**data["request"])
        from .writer import write_synthetic_capture
        write_synthetic_capture(
            context=context, request=request, preview_id=preview_id,
            preview_checksum=_sha256(root / "capture_preview.sha256"),
            phase_identity=json.loads((root / "phase3b_identity.json").read_text()),
            intake_identity=json.loads((root / "intake_identity.json").read_text()),
            recovery_identity=json.loads((root / "recovery_identity.json").read_text()),
        )
        if not mark_preview_consumed(context.control_root, preview_id).ok:
            return ErebusCaptureResult("REFUSED", False, ["PREVIEW_CONSUME_FAILED"])
        return ErebusCaptureResult("CAPTURE_VERIFIED", True)
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        from .preview_state import mark_refused
        mark_refused(root)
        return ErebusCaptureResult("REFUSED", False, [str(exc)])


def load_preview(control_root: str | Path, preview_id: str) -> ErebusCaptureResult:
    """Verify an exact immutable preview before any future capture write."""
    root = Path(control_root) / "validation" / "previews" / "erebus" / preview_id
    try:
        if load_state(root) is not PreviewState.READY:
            return ErebusCaptureResult("REFUSED", False, ["PREVIEW_NOT_READY"])
    except ValueError:
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_STATE_CORRUPT"])
    required = preview_receipt_files()
    missing = sorted(name for name in required if not (root / name).is_file() or (root / name).is_symlink())
    if missing:
        return ErebusCaptureResult("REFUSED", False, [f"PREVIEW_FILES_MISSING: {', '.join(missing)}"])
    unexpected = sorted(path.name for path in root.iterdir() if path.name not in required or not path.is_file() or path.is_symlink())
    if unexpected:
        return ErebusCaptureResult("REFUSED", False, [f"PREVIEW_FILES_UNEXPECTED: {', '.join(unexpected)}"])
    try:
        expected = json.loads((root / "capture_preview.sha256").read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_MANIFEST_INVALID"])
    if expected != _preview_manifest(root):
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_CHECKSUM_MISMATCH"])
    try:
        data = json.loads((root / "capture_preview.json").read_text(encoding="utf-8"))
        source_identity = json.loads((root / "source_identity.json").read_text(encoding="utf-8"))
        storage_identity = json.loads((root / "storage_identity.json").read_text(encoding="utf-8"))
        recovery_identity = json.loads((root / "recovery_identity.json").read_text(encoding="utf-8"))
        phase_identity = json.loads((root / "phase3b_identity.json").read_text(encoding="utf-8"))
        intake_identity = json.loads((root / "intake_identity.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_COMPONENT_INVALID"])
    if data.get("preview_id") != preview_id or "latest" in json.dumps(data).lower():
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_IDENTITY_INVALID"])
    request = data.get("request") or {}
    if (data.get("capture_id") != request.get("capture_id") or
            data.get("final_path") != str(Path(control_root) / "validation" / "erebus" / str(request.get("capture_id"))) or
            source_identity != data.get("identity") or
            recovery_identity.get("receipt_path") != str(request.get("recovery_receipt")) or
            phase_identity.get("run_id") != request.get("phase3b_run_id") or
            intake_identity.get("path") != str(request.get("intake_contract")) or
            not storage_identity.get("uuid")):
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_COMPONENT_MISMATCH"])
    repo = Path(str(request.get("repository") or ""))
    try:
        _source_identity(ErebusCaptureRequest(**request))
        recovery = data.get("recovery_receipt")
        if recovery and _receipt_sha256(Path(recovery["path"])) != (recovery["path"], recovery["sha256"]):
            return ErebusCaptureResult("REFUSED", False, ["PREVIEW_RECOVERY_DRIFT"])
        intake = data.get("intake_contract")
        if intake and (not Path(intake["path"]).is_file() or _sha256(Path(intake["path"])) != intake["sha256"]):
            return ErebusCaptureResult("REFUSED", False, ["PREVIEW_INTAKE_DRIFT"])
    except ValueError:
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_SOURCE_DRIFT"])
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_SOURCE_UNAVAILABLE"])
    return ErebusCaptureResult("PREVIEW_READY", True)


def revalidate_preview_for_execute(context: CaptureContext, preview_id: str) -> ErebusCaptureResult:
    """Recheck every external dependency before any future writer may start."""
    loaded = load_preview(context.control_root, preview_id)
    if not loaded.ok:
        root = Path(context.control_root) / "validation" / "previews" / "erebus" / preview_id
        try:
            if root.exists() and load_state(root) is PreviewState.READY:
                invalidate(root)
        except ValueError:
            pass
        return loaded
    root = Path(context.control_root) / "validation" / "previews" / "erebus" / preview_id
    payload = json.loads((root / "capture_preview.json").read_text(encoding="utf-8"))
    request = ErebusCaptureRequest(**payload["request"])
    final = Path(payload["final_path"])
    if final.exists():
        invalidate(root)
        return ErebusCaptureResult("REFUSED", False, ["FINAL_CAPTURE_EXISTS"])
    try:
        validate_storage(context.storage_resolver(), minimum_free_bytes=context.minimum_free_bytes)
        validate_recovery_receipt(context.recovery_receipt, artifact_sha256=request.maintenance_sha256, repair_commit=request.expected_commit, repair_tree=request.expected_tree)
        validate_phase3b(context.phase3b_root, request.phase3b_run_id)
        validate_intake_contract(context.intake_contract)
    except ValueError as exc:
        invalidate(root)
        return ErebusCaptureResult("REFUSED", False, [str(exc)])
    return ErebusCaptureResult("PREVIEW_READY", True)


def begin_preview_execution(control_root: str | Path, preview_id: str) -> ErebusCaptureResult:
    """Reserve an exact READY receipt for a future writer; create no capture."""
    root = preview_root(Path(control_root), preview_id)
    try:
        from .preview_state import begin_execution
        if begin_execution(root):
            return ErebusCaptureResult("EXECUTION_STARTED", True)
    except ValueError:
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_STATE_CORRUPT"])
    return ErebusCaptureResult("REFUSED", False, ["PREVIEW_NOT_READY"])


def mark_preview_consumed(control_root: str | Path, preview_id: str) -> ErebusCaptureResult:
    """Mark a reserved preview consumed after a future writer has verified it."""
    root = preview_root(Path(control_root), preview_id)
    try:
        from .preview_state import mark_consumed
        if mark_consumed(root):
            return ErebusCaptureResult("CONSUMED", True)
    except ValueError:
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_STATE_CORRUPT"])
    return ErebusCaptureResult("REFUSED", False, ["PREVIEW_NOT_EXECUTING"])


def invalidate_preview(control_root: str | Path, preview_id: str) -> ErebusCaptureResult:
    root = preview_root(Path(control_root), preview_id)
    try:
        invalidate(root)
    except OSError:
        return ErebusCaptureResult("REFUSED", False, ["PREVIEW_UNAVAILABLE"])
    return ErebusCaptureResult("INVALIDATED", True)
