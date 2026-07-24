"""Fail-closed validation for the governed Erebus intake contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ErebusCaptureIntakeIdentity

ALLOWED = {"intake_contract.json", "README.md", "manifests", "ingest_ready", "prepared", "notes"}
EXCLUDED = {"downloads", "archive", "logs", "tools"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_intake_contract(path: Path) -> ErebusCaptureIntakeIdentity:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError("INTAKE_MISMATCH: contract or sidecar missing")
    fields = sidecar.read_text().split()
    if len(fields) < 2 or fields[0] != _sha(path):
        raise ValueError("INTAKE_MISMATCH: checksum invalid")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError("INTAKE_MISMATCH: malformed JSON") from exc
    if data.get("schema_version") != 1 or data.get("intake_root_name") != "erebus-intake":
        raise ValueError("INTAKE_MISMATCH: schema or root")
    included = set(data.get("included_members") or [])
    excluded = set(data.get("excluded_members") or [])
    if included != ALLOWED or not EXCLUDED.issubset(excluded):
        raise ValueError("INTAKE_MISMATCH: member policy")
    if data.get("bypass_allowed") is not False or data.get("mount_guard_required") is not True:
        raise ValueError("INTAKE_MISMATCH: bypass or mount guard")
    serialized = json.dumps(data).lower()
    if any(token in serialized for token in ("password", "secret", "api_key")):
        raise ValueError("INTAKE_MISMATCH: secret material")
    return ErebusCaptureIntakeIdentity(str(path), _sha(path))
