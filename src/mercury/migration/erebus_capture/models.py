"""Structured inputs and decisions for Erebus source captures."""

from dataclasses import dataclass, field
from enum import StrEnum


class CaptureStatus(StrEnum):
    PREVIEW_READY = "PREVIEW_READY"
    REFUSED = "REFUSED"
    INVALID_REQUEST = "INVALID_REQUEST"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    STORAGE_MISMATCH = "STORAGE_MISMATCH"
    PHASE3B_MISMATCH = "PHASE3B_MISMATCH"
    INTAKE_MISMATCH = "INTAKE_MISMATCH"
    RECOVERY_MISMATCH = "RECOVERY_MISMATCH"
    PREVIEW_CORRUPT = "PREVIEW_CORRUPT"
    PREVIEW_CONSUMED = "PREVIEW_CONSUMED"
    FINAL_PATH_EXISTS = "FINAL_PATH_EXISTS"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ErebusCaptureRefusal:
    code: CaptureStatus
    message: str
    failed_gate: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ErebusCaptureSourceIdentity:
    repository: str
    branch: str
    head: str
    tree: str
    origin_main: str


@dataclass(frozen=True)
class ErebusCaptureStorageIdentity:
    label: str
    uuid: str
    mount_path: str
    free_bytes: int
    source_host: bool


@dataclass(frozen=True)
class ErebusCapturePhase3BIdentity:
    run_id: str
    root: str


@dataclass(frozen=True)
class ErebusCaptureIntakeIdentity:
    path: str
    sha256: str


@dataclass(frozen=True)
class ErebusCaptureRecoveryIdentity:
    receipt_path: str
    receipt_sha256: str
    maintenance_sha256: str


@dataclass(frozen=True)
class ErebusCaptureSafetyDecision:
    status: CaptureStatus
    execute_authorized: bool


@dataclass(frozen=True)
class ErebusCapturePreviewManifest:
    preview_id: str
    capture_id: str
    final_path: str
    member_paths: tuple[str, ...]


@dataclass(frozen=True)
class ErebusCaptureRequest:
    preview_id: str
    repository: str
    capture_id: str
    expected_commit: str
    expected_tree: str
    phase3b_run_id: str
    maintenance_sha256: str
    control_root: str = ""
    recovery_receipt: str = ""
    intake_contract: str = ""
    supersedes_capture_id: str = "erebus_destination_candidate_3f1bb5b_20260722T150930Z"


@dataclass
class ErebusCapturePreview:
    preview_id: str
    request: ErebusCaptureRequest
    ok: bool
    errors: list[str] = field(default_factory=list)
    path: str = ""
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class ErebusCaptureResult:
    classification: str
    ok: bool
    errors: list[str] = field(default_factory=list)
