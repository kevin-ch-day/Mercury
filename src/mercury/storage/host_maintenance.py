"""Host-local Mercury maintenance state (outside the operator HDD)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path

ENV_HOST_STATE = "MERCURY_HOST_MAINTENANCE_PATH"
ENV_TEST_ISOLATION = "MERCURY_TEST_ISOLATION"


def default_host_maintenance_path() -> Path:
    override = os.environ.get(ENV_HOST_STATE)
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "mercury" / "host_maintenance.json"


def assert_not_live_mercury_path(path: Path | str, *, purpose: str = "write") -> None:
    """Refuse known live Mercury paths when test isolation is enabled."""
    if os.environ.get(ENV_TEST_ISOLATION, "").strip() not in {"1", "true", "yes"}:
        return
    target = Path(path).expanduser()
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target
    live_files = {
        (Path.home() / ".local" / "share" / "mercury" / "host_maintenance.json").resolve(),
        (Path.home() / ".local" / "share" / "mercury" / "transition_ledger.jsonl").resolve(),
    }
    live_roots = {
        Path("/mnt/MERCURY_DATA_V2").resolve(),
        Path("/mnt/MERCURY_DATA_USB").resolve(),
    }
    if resolved in live_files:
        raise RuntimeError(
            f"TEST ISOLATION: refused {purpose} of live Mercury path {resolved}"
        )
    for root in live_roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        raise RuntimeError(
            f"TEST ISOLATION: refused {purpose} under live Mercury path {root}"
        )


@dataclass
class HostMaintenanceState:
    storage_availability: str = "attached"  # attached | mounted | detaching | detached
    writes_allowed: bool = True
    active_write_role: str = "primary"
    # Legacy flag: source-side “rehearsal activity” marker (kept for older readers).
    destination_rehearsal_in_progress: bool = False
    # Explicit semantics (preferred):
    destination_rehearsal_planned: bool = False
    destination_rehearsal_active: bool = False
    source_detach_preparation: bool = False
    package_id: str = ""
    package_verification_status: str = ""
    updated_at_utc: str = ""
    notes: str = ""
    # Post-package delta (host-local; does not mutate sealed package evidence).
    source_writes_resumed_after_package: bool = False
    source_writes_resumed_at: str = ""
    source_delta_started_at: str = ""  # compat alias of source_writes_resumed_at
    source_delta_relative_to_package_id: str = ""
    source_delta_reason: str = ""
    # Recovery artifacts (DB backups / Git captures) created after the package.
    recovery_artifacts_created_after_package: bool = False
    first_post_package_artifact_at: str = ""
    first_post_package_artifact_type: str = ""  # database_backup | git_capture
    first_post_package_artifact_id: str = ""
    # Production/source data mutations (ingestion, queues, etc.) — not backups.
    source_data_changed_since_package: bool = False
    source_data_first_change_at: str = ""
    source_data_first_change_operation: str = ""
    # Development destinations changed (e.g. prod→dev sync).
    development_state_changed_since_package: bool = False
    development_state_first_change_at: str = ""
    development_state_first_change_operation: str = ""
    # Legacy compatibility: previously conflated recovery artifacts with "source changed".
    # Prefer recovery_artifacts_created_after_package / source_data_changed_since_package.
    source_changed_since_package: bool = False
    source_delta_first_write_at: str = ""
    source_delta_first_write_operation: str = ""
    source_delta_first_artifact_id: str = ""
    # Set by a successful Safe Disconnect; cleared on reconnect.
    intentional_safe_disconnect: bool = False
    last_safe_disconnect_result: str = ""


def _coerce_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def load_host_maintenance(path: Path | None = None) -> HostMaintenanceState:
    path = path or default_host_maintenance_path()
    if not path.is_file():
        return HostMaintenanceState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return HostMaintenanceState(writes_allowed=False, storage_availability="unknown")

    rehearsal_legacy = _coerce_bool(data.get("destination_rehearsal_in_progress", False))
    rehearsal_active = data.get("destination_rehearsal_active")
    if rehearsal_active is None:
        rehearsal_active = rehearsal_legacy
    else:
        rehearsal_active = _coerce_bool(rehearsal_active)

    package_status = str(data.get("package_verification_status") or "")
    package_id = str(data.get("package_id") or "")
    planned = data.get("destination_rehearsal_planned")
    if planned is None:
        planned = bool(package_id) or package_status == "DESTINATION_PACKAGE_VERIFIED"
    else:
        planned = _coerce_bool(planned)

    availability = str(data.get("storage_availability") or "attached")
    detach_prep = data.get("source_detach_preparation")
    if detach_prep is None:
        detach_prep = availability == "detaching"
    else:
        detach_prep = _coerce_bool(detach_prep)

    return HostMaintenanceState(
        storage_availability=availability,
        writes_allowed=_coerce_bool(data.get("writes_allowed", True)),
        active_write_role=str(data.get("active_write_role") or "primary"),
        destination_rehearsal_in_progress=rehearsal_legacy or bool(rehearsal_active),
        destination_rehearsal_planned=bool(planned),
        destination_rehearsal_active=bool(rehearsal_active),
        source_detach_preparation=bool(detach_prep),
        package_id=package_id,
        package_verification_status=package_status,
        updated_at_utc=str(data.get("updated_at_utc") or ""),
        notes=str(data.get("notes") or ""),
        source_writes_resumed_after_package=_coerce_bool(
            data.get("source_writes_resumed_after_package", False)
        ),
        source_writes_resumed_at=str(
            data.get("source_writes_resumed_at")
            or data.get("source_delta_started_at")
            or ""
        ),
        source_delta_started_at=str(
            data.get("source_delta_started_at")
            or data.get("source_writes_resumed_at")
            or ""
        ),
        source_delta_relative_to_package_id=str(
            data.get("source_delta_relative_to_package_id") or ""
        ),
        source_delta_reason=str(data.get("source_delta_reason") or ""),
        recovery_artifacts_created_after_package=_coerce_bool(
            data.get("recovery_artifacts_created_after_package")
            if data.get("recovery_artifacts_created_after_package") is not None
            else data.get("source_changed_since_package", False)
        ),
        first_post_package_artifact_at=str(
            data.get("first_post_package_artifact_at")
            or data.get("source_delta_first_write_at")
            or ""
        ),
        first_post_package_artifact_type=str(
            data.get("first_post_package_artifact_type") or ""
        ),
        first_post_package_artifact_id=str(
            data.get("first_post_package_artifact_id")
            or data.get("source_delta_first_artifact_id")
            or ""
        ),
        source_data_changed_since_package=_coerce_bool(
            data.get("source_data_changed_since_package", False)
        ),
        source_data_first_change_at=str(data.get("source_data_first_change_at") or ""),
        source_data_first_change_operation=str(
            data.get("source_data_first_change_operation") or ""
        ),
        development_state_changed_since_package=_coerce_bool(
            data.get("development_state_changed_since_package", False)
        ),
        development_state_first_change_at=str(
            data.get("development_state_first_change_at") or ""
        ),
        development_state_first_change_operation=str(
            data.get("development_state_first_change_operation") or ""
        ),
        source_changed_since_package=_coerce_bool(
            data.get("source_changed_since_package")
            if data.get("source_changed_since_package") is not None
            else data.get("recovery_artifacts_created_after_package", False)
        ),
        source_delta_first_write_at=str(
            data.get("source_delta_first_write_at")
            or data.get("first_post_package_artifact_at")
            or ""
        ),
        source_delta_first_write_operation=str(
            data.get("source_delta_first_write_operation")
            or data.get("first_post_package_artifact_type")
            or ""
        ),
        source_delta_first_artifact_id=str(
            data.get("source_delta_first_artifact_id")
            or data.get("first_post_package_artifact_id")
            or ""
        ),
        intentional_safe_disconnect=_coerce_bool(
            data.get("intentional_safe_disconnect", False)
        ),
        last_safe_disconnect_result=str(
            data.get("last_safe_disconnect_result") or ""
        ),
    )


def save_host_maintenance(
    state: HostMaintenanceState, path: Path | None = None
) -> Path:
    path = path or default_host_maintenance_path()
    assert_not_live_mercury_path(path, purpose="host_maintenance write")
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Keep legacy and explicit rehearsal flags aligned (either may be set by callers).
    active = bool(
        state.destination_rehearsal_active or state.destination_rehearsal_in_progress
    )
    state.destination_rehearsal_active = active
    state.destination_rehearsal_in_progress = active
    payload = {f.name: getattr(state, f.name) for f in fields(state)}
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    if state.source_detach_preparation:
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
        f"(HDD detach / destination rehearsal; restore writes after reconnect)"
    )


def path_is_under_primary_mount(path: Path | str, *, mount: Path | str | None = None) -> bool:
    """True when ``path`` resolves beneath the Mercury primary mount (HDD)."""
    from mercury.core.storage_roles import DEFAULT_PRIMARY_MOUNT

    target = Path(path).expanduser()
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target if target.is_absolute() else Path.cwd() / target
    if mount is None:
        try:
            from mercury.core.storage_roots import load_storage_config

            mount = load_storage_config(warn_deprecated=False).primary.mount_path
        except Exception:
            mount = DEFAULT_PRIMARY_MOUNT
    base = Path(mount).expanduser()
    try:
        base_resolved = base.resolve()
    except OSError:
        base_resolved = base
    try:
        resolved.relative_to(base_resolved)
        return True
    except ValueError:
        return False


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
        source_detach_preparation=True,
        destination_rehearsal_planned=True,
        destination_rehearsal_active=True,
        destination_rehearsal_in_progress=True,
        package_id=package_id,
        package_verification_status=package_verification_status,
        notes="Mercury HDD detach in progress; destination cutover is NOT complete.",
    )
    save_host_maintenance(state, path=path)
    return state


def mark_detached(
    path: Path | None = None,
    *,
    result_state: str = "",
    intentional: bool = True,
) -> HostMaintenanceState:
    state = load_host_maintenance(path)
    state.storage_availability = "detached"
    state.writes_allowed = False
    state.active_write_role = "none"
    state.source_detach_preparation = False
    state.destination_rehearsal_active = True
    state.destination_rehearsal_in_progress = True
    state.destination_rehearsal_planned = True
    state.intentional_safe_disconnect = bool(intentional)
    if result_state:
        state.last_safe_disconnect_result = result_state
    state.notes = (
        "Mercury HDD detached for destination rehearsal; refuse HDD-backed writes. "
        "Destination cutover is NOT complete."
    )
    save_host_maintenance(state, path=path)
    return state


def intentional_safe_disconnect_active(
    state: HostMaintenanceState | None = None,
) -> bool:
    """True after a successful Safe Disconnect awaiting physical move.

    Also recognizes hosts detached before ``intentional_safe_disconnect`` existed
    when the package is verified and destination-rehearsal notes are present.
    """
    state = state or load_host_maintenance()
    availability = str(getattr(state, "storage_availability", "") or "")
    if availability != "detached":
        return False
    if bool(getattr(state, "writes_allowed", False)):
        return False
    role = str(getattr(state, "active_write_role", "none") or "none")
    if role not in {"", "none"}:
        return False
    if bool(getattr(state, "intentional_safe_disconnect", False)):
        return True
    verified = (
        str(getattr(state, "package_verification_status", "") or "")
        == "DESTINATION_PACKAGE_VERIFIED"
    )
    rehearsal = bool(
        getattr(state, "destination_rehearsal_active", False)
        or getattr(state, "destination_rehearsal_in_progress", False)
    )
    notes = str(getattr(state, "notes", "") or "").lower()
    return bool(
        verified
        and rehearsal
        and ("detached for destination" in notes or "destination rehearsal" in notes)
    )


def mark_recovery_artifact_after_package(
    path: Path | None = None,
    *,
    artifact_type: str,
    artifact_id: str = "",
    operation: str = "",
) -> HostMaintenanceState:
    """Record that a recovery artifact (backup/Git capture) was created after package.

    Does **not** claim production source data changed. Package evidence is untouched.
    """
    state = load_host_maintenance(path)
    if not state.source_writes_resumed_after_package or not state.package_id:
        return state
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = not state.recovery_artifacts_created_after_package
    state.recovery_artifacts_created_after_package = True
    # Legacy mirror for older readers/dashboard wording.
    state.source_changed_since_package = True
    if first:
        state.first_post_package_artifact_at = now
        state.first_post_package_artifact_type = artifact_type
        state.first_post_package_artifact_id = artifact_id
        state.source_delta_first_write_at = now
        state.source_delta_first_write_operation = operation or artifact_type
        state.source_delta_first_artifact_id = artifact_id
        note = (
            f"First recovery artifact after package: type={artifact_type} "
            f"id={artifact_id or 'n/a'} at {now}."
        )
        if note not in (state.notes or ""):
            state.notes = f"{state.notes} {note}".strip() if state.notes else note
    save_host_maintenance(state, path=path)
    return state


def mark_development_state_changed_since_package(
    path: Path | None = None,
    *,
    operation: str = "prod_to_dev_sync",
    event_id: str = "",
) -> HostMaintenanceState:
    """Record that development destinations changed after the package (e.g. sync)."""
    state = load_host_maintenance(path)
    if not state.source_writes_resumed_after_package or not state.package_id:
        return state
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = not state.development_state_changed_since_package
    state.development_state_changed_since_package = True
    if first:
        state.development_state_first_change_at = now
        state.development_state_first_change_operation = operation
        note = (
            f"First development-state change after package: operation={operation} "
            f"event={event_id or 'n/a'} at {now}."
        )
        if note not in (state.notes or ""):
            state.notes = f"{state.notes} {note}".strip() if state.notes else note
    save_host_maintenance(state, path=path)
    return state


def mark_source_data_changed_since_package(
    path: Path | None = None,
    *,
    operation: str = "",
) -> HostMaintenanceState:
    """Record that production/source data mutated after the package (not backups)."""
    state = load_host_maintenance(path)
    if not state.source_writes_resumed_after_package or not state.package_id:
        return state
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = not state.source_data_changed_since_package
    state.source_data_changed_since_package = True
    if first:
        state.source_data_first_change_at = now
        state.source_data_first_change_operation = operation
    save_host_maintenance(state, path=path)
    return state


def mark_source_changed_since_package(
    path: Path | None = None,
    *,
    operation: str = "",
    artifact_id: str = "",
) -> HostMaintenanceState:
    """Backward-compatible marker for post-package recovery artifact creation.

    Prefer ``mark_recovery_artifact_after_package`` or
    ``mark_development_state_changed_since_package`` for new call sites.
    Sync-like operations are routed to development-state markers.
    """
    op = (operation or "").strip()
    if "sync" in op.lower():
        return mark_development_state_changed_since_package(
            path, operation=op or "prod_to_dev_sync", event_id=artifact_id
        )
    artifact_type = "git_capture" if "git" in op.lower() else "database_backup"
    return mark_recovery_artifact_after_package(
        path,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        operation=op,
    )
