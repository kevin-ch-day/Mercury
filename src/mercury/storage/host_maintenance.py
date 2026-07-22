"""Host-local Mercury maintenance state (outside the operator HDD)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path

ENV_HOST_STATE = "MERCURY_HOST_MAINTENANCE_PATH"


def default_host_maintenance_path() -> Path:
    override = os.environ.get(ENV_HOST_STATE)
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "mercury" / "host_maintenance.json"


@dataclass
class HostMaintenanceState:
    storage_availability: str = "attached"  # attached | detaching | detached
    writes_allowed: bool = True
    active_write_role: str = "primary"
    destination_rehearsal_in_progress: bool = False
    package_id: str = ""
    package_verification_status: str = ""
    updated_at_utc: str = ""
    notes: str = ""


def load_host_maintenance(path: Path | None = None) -> HostMaintenanceState:
    path = path or default_host_maintenance_path()
    if not path.is_file():
        return HostMaintenanceState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return HostMaintenanceState(writes_allowed=False, storage_availability="unknown")
    return HostMaintenanceState(
        storage_availability=str(data.get("storage_availability") or "attached"),
        writes_allowed=bool(data.get("writes_allowed", True)),
        active_write_role=str(data.get("active_write_role") or "primary"),
        destination_rehearsal_in_progress=bool(
            data.get("destination_rehearsal_in_progress", False)
        ),
        package_id=str(data.get("package_id") or ""),
        package_verification_status=str(data.get("package_verification_status") or ""),
        updated_at_utc=str(data.get("updated_at_utc") or ""),
        notes=str(data.get("notes") or ""),
    )


def save_host_maintenance(
    state: HostMaintenanceState, path: Path | None = None
) -> Path:
    path = path or default_host_maintenance_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = path.with_suffix(".partial")
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def writes_allowed(state: HostMaintenanceState | None = None) -> bool:
    state = state or load_host_maintenance()
    if state.storage_availability in {"detaching", "detached"}:
        return False
    return bool(state.writes_allowed)


def refuse_if_hdd_writes_disabled(action: str = "HDD-backed write") -> None:
    """Raise RuntimeError when host maintenance refuses Mercury HDD writes."""
    state = load_host_maintenance()
    if writes_allowed(state):
        return
    raise RuntimeError(
        f"{action} refused: host maintenance "
        f"storage_availability={state.storage_availability} "
        f"writes_allowed={state.writes_allowed} "
        f"(destination rehearsal / HDD detach in progress; cutover is NOT complete)"
    )


def mark_detaching(
    *,
    package_id: str,
    package_verification_status: str,
    path: Path | None = None,
) -> HostMaintenanceState:
    state = HostMaintenanceState(
        storage_availability="detaching",
        writes_allowed=False,
        active_write_role="none",
        destination_rehearsal_in_progress=True,
        package_id=package_id,
        package_verification_status=package_verification_status,
        notes="Mercury HDD detach in progress; destination cutover is NOT complete.",
    )
    save_host_maintenance(state, path=path)
    return state


def mark_detached(path: Path | None = None) -> HostMaintenanceState:
    state = load_host_maintenance(path)
    state.storage_availability = "detached"
    state.writes_allowed = False
    state.active_write_role = "none"
    state.destination_rehearsal_in_progress = True
    state.notes = (
        "Mercury HDD detached for destination rehearsal; refuse HDD-backed writes. "
        "Destination cutover is NOT complete."
    )
    save_host_maintenance(state, path=path)
    return state
