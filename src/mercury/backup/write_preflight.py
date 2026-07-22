"""Top-level preflight for HDD-backed backup writes (fail closed on detach)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    load_host_maintenance,
    writes_allowed,
)


@dataclass(frozen=True)
class BackupWritePreflight:
    """Whether any Mercury HDD-backed backup write may begin."""

    allowed: bool
    reason: str = ""
    storage_availability: str = "attached"
    writes_allowed: bool = True
    active_write_role: str = "primary"
    destination_rehearsal_in_progress: bool = False
    detail_lines: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()

    @property
    def is_detach_maintenance(self) -> bool:
        return self.storage_availability in {"detaching", "detached"} or not self.writes_allowed


def assess_backup_write_preflight(
    *,
    host: HostMaintenanceState | None = None,
) -> BackupWritePreflight:
    """Refuse all HDD-backed backup writes when host maintenance disables them."""
    state = host or load_host_maintenance()
    if writes_allowed(state):
        return BackupWritePreflight(
            allowed=True,
            storage_availability=state.storage_availability,
            writes_allowed=True,
            active_write_role=state.active_write_role,
            destination_rehearsal_in_progress=state.destination_rehearsal_in_progress,
        )

    reason = "Mercury HDD detach maintenance is active"
    if state.storage_availability == "detached":
        reason = "Mercury HDD is detached; writes remain disabled"
    elif state.storage_availability == "attached" and not state.writes_allowed:
        reason = "Mercury HDD is attached but writes remain disabled pending reconnect restore"

    details = (
        f"Storage state:   {state.storage_availability}",
        f"Writes allowed:  {'yes' if state.writes_allowed else 'no'}",
        f"Active writer:   {state.active_write_role or 'none'}",
    )
    next_steps = (
        "Reconnect and validate the Mercury HDD",
        "Restore Mercury writes through the guided reconnect workflow",
        "Return to Backup Operations",
    )
    return BackupWritePreflight(
        allowed=False,
        reason=reason,
        storage_availability=state.storage_availability,
        writes_allowed=False,
        active_write_role=state.active_write_role or "none",
        destination_rehearsal_in_progress=state.destination_rehearsal_in_progress,
        detail_lines=details,
        next_steps=next_steps,
    )


def default_host_local_refusal_root() -> Path:
    import os

    override = os.environ.get("MERCURY_REFUSED_OPERATIONS_DIR")
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "mercury" / "refused_operations"


def is_host_local_refusal_record(payload: dict) -> bool:
    """True when a JSON object is an audit-only host-local refusal (never backup evidence)."""
    return (
        payload.get("evidence_class") == "host_local_refusal"
        and payload.get("not_backup_evidence") is True
        and payload.get("not_handoff_evidence") is True
    )


def is_governed_full_backup_receipt(payload: dict) -> bool:
    """Governed HDD receipts are successful/partial runs with written artifacts.

    REFUSED / zero-written maintenance artifacts are never authoritative backup evidence.
    """
    if is_host_local_refusal_record(payload):
        return False
    outcome = str(payload.get("outcome") or "")
    written = int(payload.get("overall_written") or 0)
    if outcome == "REFUSED" or written <= 0:
        return False
    if payload.get("global_refusal") is True:
        return False
    if str(payload.get("backup_artifacts_result") or "") in {
        "NOT_ATTEMPTED",
        "FAIL",
        "PENDING",
    } and written <= 0:
        return False
    return outcome in {"PASS", "PARTIAL"} and written > 0
