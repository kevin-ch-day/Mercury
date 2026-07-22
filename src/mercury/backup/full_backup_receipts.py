"""Classify and plan quarantine for full-backup run receipts (observe / plan only)."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from mercury.backup.write_preflight import (
    is_governed_full_backup_receipt,
    is_host_local_refusal_record,
)
from mercury.core.storage_roles import CONTROL_DIRNAME

INVALID_MAINTENANCE_CLASS = "invalid_maintenance_mode_artifact"
QUARANTINE_RELATIVE = Path(CONTROL_DIRNAME) / "quarantine" / "invalid_maintenance_receipts"


@dataclass(frozen=True)
class FullBackupReceiptClassification:
    path: Path
    run_id: str
    classification: str  # governed | invalid_maintenance | host_local_refusal | unreadable | unknown
    outcome: str = ""
    overall_written: int = 0
    notes: tuple[str, ...] = ()
    governed: bool = False


@dataclass
class FullBackupReceiptQuarantinePlan:
    """Dry-run quarantine plan — never moves files by itself."""

    mount_root: Path
    quarantine_dir: Path
    entries: list[FullBackupReceiptClassification] = field(default_factory=list)

    @property
    def invalid_count(self) -> int:
        return sum(1 for e in self.entries if e.classification == INVALID_MAINTENANCE_CLASS)

    @property
    def governed_count(self) -> int:
        return sum(1 for e in self.entries if e.governed)


def classify_full_backup_receipt_payload(
    payload: dict[str, Any],
    *,
    path: Path | None = None,
) -> FullBackupReceiptClassification:
    run_id = str(payload.get("run_id") or (path.stem if path else ""))
    written = int(payload.get("overall_written") or 0)
    outcome = str(payload.get("outcome") or "")
    if is_host_local_refusal_record(payload):
        return FullBackupReceiptClassification(
            path=path or Path("."),
            run_id=run_id,
            classification="host_local_refusal",
            outcome=outcome,
            overall_written=written,
            notes=("host-local audit only; never handoff evidence",),
            governed=False,
        )
    if is_governed_full_backup_receipt(payload):
        return FullBackupReceiptClassification(
            path=path or Path("."),
            run_id=run_id,
            classification="governed",
            outcome=outcome,
            overall_written=written,
            governed=True,
        )
    if outcome == "REFUSED" or written <= 0 or payload.get("global_refusal") is True:
        return FullBackupReceiptClassification(
            path=path or Path("."),
            run_id=run_id,
            classification=INVALID_MAINTENANCE_CLASS,
            outcome=outcome,
            overall_written=written,
            notes=(
                "Not authoritative backup evidence.",
                "Safe to quarantine later under .mercury_control/quarantine/invalid_maintenance_receipts/.",
                "Retain checksum sidecar; do not delete.",
            ),
            governed=False,
        )
    return FullBackupReceiptClassification(
        path=path or Path("."),
        run_id=run_id,
        classification="unknown",
        outcome=outcome,
        overall_written=written,
        notes=("manual review required",),
        governed=False,
    )


def classify_full_backup_receipt_file(path: Path) -> FullBackupReceiptClassification:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return FullBackupReceiptClassification(
            path=path,
            run_id=path.stem,
            classification="unreadable",
            notes=(str(exc),),
            governed=False,
        )
    if not isinstance(payload, dict):
        return FullBackupReceiptClassification(
            path=path,
            run_id=path.stem,
            classification="unreadable",
            notes=("receipt JSON root is not an object",),
            governed=False,
        )
    return classify_full_backup_receipt_payload(payload, path=path)


def list_full_backup_run_receipts(control_root: Path) -> list[Path]:
    directory = control_root / "full_backup_runs"
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def plan_quarantine_invalid_full_backup_receipts(
    mount_root: Path,
    *,
    control_root: Path | None = None,
) -> FullBackupReceiptQuarantinePlan:
    """Observe-only plan for moving invalid maintenance receipts into quarantine."""
    control = control_root or (mount_root / CONTROL_DIRNAME)
    plan = FullBackupReceiptQuarantinePlan(
        mount_root=mount_root,
        quarantine_dir=mount_root / QUARANTINE_RELATIVE,
    )
    for path in list_full_backup_run_receipts(control):
        plan.entries.append(classify_full_backup_receipt_file(path))
    return plan
