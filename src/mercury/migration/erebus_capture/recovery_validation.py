"""Fail-closed validation of the host-local maintenance recovery receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ErebusCaptureRecoveryIdentity

PATH = "src/database/db_query/virustotal_queries/reports/maintenance.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_recovery_receipt(path: Path, *, artifact_sha256: str, repair_commit: str, repair_tree: str) -> ErebusCaptureRecoveryIdentity:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError("RECOVERY_MISMATCH: receipt or sidecar missing")
    fields = sidecar.read_text(encoding="utf-8").split()
    if len(fields) < 2 or fields[0] != _sha(path):
        raise ValueError("RECOVERY_MISMATCH: receipt checksum invalid")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("RECOVERY_MISMATCH: receipt JSON invalid") from exc
    checks = {
        "source path": data.get("source_relative_path") == PATH,
        "artifact hash": data.get("artifact_sha256") == artifact_sha256,
        "repair commit": data.get("repair_commit") == repair_commit,
        "repair tree": data.get("repair_tree") == repair_tree,
        "original ignore": data.get("original_ignore_rule") == "reports/",
        "repaired ignore": data.get("repaired_ignore_rule") == "/reports/",
        "tracked": data.get("tracked") is True,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError("RECOVERY_MISMATCH: " + ", ".join(failed))
    return ErebusCaptureRecoveryIdentity(str(path), _sha(path), artifact_sha256)
