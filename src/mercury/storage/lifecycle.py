"""Mercury HDD storage lifecycle states (physical + policy, user-facing labels)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from mercury.core.storage_roles import DEFAULT_PRIMARY_UUID
from mercury.menu.options import ACTION_HDD_STORAGE, main_menu_hint
from mercury.storage.host_maintenance import HostMaintenanceState, load_host_maintenance, writes_allowed


class StorageLifecycleState(str, Enum):
    ATTACHED_WRITER_ENABLED = "ATTACHED_WRITER_ENABLED"
    ATTACHED_WRITER_DISABLED = "ATTACHED_WRITER_DISABLED"
    PREPARING_TO_DISCONNECT = "PREPARING_TO_DISCONNECT"
    READY_TO_DISCONNECT = "READY_TO_DISCONNECT"
    DETACHED = "DETACHED"
    ATTACHED_READ_ONLY = "ATTACHED_READ_ONLY"
    ATTACHED_UNVALIDATED = "ATTACHED_UNVALIDATED"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    DEVICE_IDENTITY_MISMATCH = "DEVICE_IDENTITY_MISMATCH"
    RECONNECT_VALIDATED = "RECONNECT_VALIDATED"


class MigrationHostRole(str, Enum):
    SOURCE_OPERATION = "SOURCE_OPERATION"
    DESTINATION_REHEARSAL = "DESTINATION_REHEARSAL"
    DESTINATION_VALIDATED = "DESTINATION_VALIDATED"
    FINAL_CUTOVER = "FINAL_CUTOVER"


LIFECYCLE_LABELS: dict[StorageLifecycleState, str] = {
    StorageLifecycleState.ATTACHED_WRITER_ENABLED: "Mounted · backups enabled",
    StorageLifecycleState.ATTACHED_WRITER_DISABLED: "Mounted · writes disabled",
    StorageLifecycleState.PREPARING_TO_DISCONNECT: "Mounted · preparing for safe disconnect",
    StorageLifecycleState.READY_TO_DISCONNECT: "Mounted · safe disconnect ready",
    StorageLifecycleState.DETACHED: "Mercury HDD not connected",
    StorageLifecycleState.ATTACHED_READ_ONLY: "Mounted read-only · inspection mode",
    StorageLifecycleState.ATTACHED_UNVALIDATED: "Mounted · not yet validated",
    StorageLifecycleState.DEVICE_NOT_FOUND: "Mercury HDD not found",
    StorageLifecycleState.DEVICE_IDENTITY_MISMATCH: "Wrong or unrecognized device",
    StorageLifecycleState.RECONNECT_VALIDATED: "Mounted · reconnect validated · writes still off",
}


@dataclass(frozen=True)
class StorageLifecycleSnapshot:
    state: StorageLifecycleState
    host_role: MigrationHostRole
    label: str
    recommended: str
    device_label: str = ""
    device_model: str = ""
    device_uuid: str = ""
    filesystem: str = ""
    mount: str = ""
    package_id: str = ""
    package_status: str = ""
    writes_allowed: bool = False
    active_write_role: str = "none"
    mounted: bool = False
    notes: tuple[str, ...] = ()
    disconnect_blocked: bool = False


def migration_host_role(host: HostMaintenanceState | None = None) -> MigrationHostRole:
    host = host or load_host_maintenance()
    if host.destination_rehearsal_in_progress:
        if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED":
            return MigrationHostRole.DESTINATION_REHEARSAL
        return MigrationHostRole.DESTINATION_REHEARSAL
    if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED" and not host.writes_allowed:
        return MigrationHostRole.DESTINATION_REHEARSAL
    return MigrationHostRole.SOURCE_OPERATION


def _safe_disconnect_ready(*, mutate_host: bool = False) -> bool:
    try:
        from mercury.storage.detach_wizard import run_detach_preflight

        pre = run_detach_preflight(skip_log_redirect=True, mutate_host=mutate_host)
    except OSError:
        return False
    return pre.result_state == "PREFLIGHT_OK"


def assess_storage_lifecycle(
    *,
    host: HostMaintenanceState | None = None,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    probe_disconnect: bool = True,
) -> StorageLifecycleSnapshot:
    """Derive lifecycle state from host maintenance + block device (observe-only)."""
    host = host or load_host_maintenance()
    role = migration_host_role(host)
    allowed = writes_allowed(host)
    device_label = ""
    device_model = ""
    device_uuid = ""
    filesystem = ""
    mount = ""
    mounted = False
    identity_ok = True
    device_found = False

    try:
        from mercury.storage.block_device import resolve_mercury_block_device

        resolved = resolve_mercury_block_device(require_mounted=False)
        if resolved.identity:
            device_found = True
            ident = resolved.identity
            device_label = ident.label or "MERCURY_DATA_V2"
            device_model = ident.model or ""
            device_uuid = ident.uuid or ""
            filesystem = ident.fstype or ""
            mount = ident.mountpoint or ""
            mounted = bool(ident.mountpoint)
            if expected_uuid and ident.uuid and ident.uuid != expected_uuid:
                identity_ok = False
        elif resolved.errors:
            # UUID configured but device missing / mismatch signals.
            joined = " ".join(resolved.errors).lower()
            if "mismatch" in joined or "wrong" in joined:
                identity_ok = False
    except OSError:
        pass

    package_id = host.package_id
    package_status = host.package_verification_status
    disconnect_ready = False
    if probe_disconnect and not allowed and mounted and identity_ok and device_found:
        disconnect_ready = _safe_disconnect_ready()

    if not identity_ok and device_found:
        state = StorageLifecycleState.DEVICE_IDENTITY_MISMATCH
    elif host.storage_availability == "detached":
        state = StorageLifecycleState.DETACHED
        mounted = False
    elif not device_found:
        state = StorageLifecycleState.DEVICE_NOT_FOUND
    elif not mounted:
        state = StorageLifecycleState.ATTACHED_UNVALIDATED
    elif host.storage_availability == "detaching" or not allowed:
        if disconnect_ready:
            state = StorageLifecycleState.READY_TO_DISCONNECT
        elif "reconnect" in (host.notes or "").lower():
            state = StorageLifecycleState.RECONNECT_VALIDATED
        elif "read-only" in (host.notes or "").lower() or host.destination_rehearsal_in_progress:
            state = (
                StorageLifecycleState.ATTACHED_READ_ONLY
                if "read-only" in (host.notes or "").lower()
                else StorageLifecycleState.PREPARING_TO_DISCONNECT
                if host.storage_availability == "detaching"
                else StorageLifecycleState.ATTACHED_WRITER_DISABLED
            )
        elif host.storage_availability == "detaching":
            state = StorageLifecycleState.PREPARING_TO_DISCONNECT
        else:
            state = StorageLifecycleState.ATTACHED_WRITER_DISABLED
    else:
        state = StorageLifecycleState.ATTACHED_WRITER_ENABLED

    # Detached overrides when host says so.
    if host.storage_availability == "detached":
        state = StorageLifecycleState.DETACHED
        mounted = False

    recommended = recommended_next_action(
        state,
        role=role,
        package_verified=package_status == "DESTINATION_PACKAGE_VERIFIED",
    )
    disconnect_blocked = bool(
        probe_disconnect
        and not allowed
        and mounted
        and identity_ok
        and device_found
        and not disconnect_ready
        and host.storage_availability == "detaching"
    )
    return StorageLifecycleSnapshot(
        state=state,
        host_role=role,
        label=LIFECYCLE_LABELS[state],
        recommended=recommended,
        device_label=device_label,
        device_model=device_model,
        device_uuid=device_uuid,
        filesystem=filesystem,
        mount=mount,
        package_id=package_id,
        package_status=package_status,
        writes_allowed=allowed,
        active_write_role=host.active_write_role,
        mounted=mounted,
        disconnect_blocked=disconnect_blocked,
    )


def recommended_next_action(
    state: StorageLifecycleState,
    *,
    role: MigrationHostRole = MigrationHostRole.SOURCE_OPERATION,
    package_verified: bool = False,
) -> str:
    if state == StorageLifecycleState.READY_TO_DISCONNECT or (
        state in {StorageLifecycleState.ATTACHED_WRITER_DISABLED, StorageLifecycleState.PREPARING_TO_DISCONNECT}
        and package_verified
    ):
        return "Safe disconnect Mercury HDD"
    if state in {StorageLifecycleState.DETACHED, StorageLifecycleState.DEVICE_NOT_FOUND}:
        return "Attach the WDC Mercury HDD, then choose Reconnect"
    if state == StorageLifecycleState.DEVICE_IDENTITY_MISMATCH:
        return "Attach the correct Mercury HDD (UUID match), then choose Reconnect"
    if state == StorageLifecycleState.ATTACHED_READ_ONLY:
        return "Verify package and configure destination"
    if state == StorageLifecycleState.RECONNECT_VALIDATED:
        return "Enable Mercury writes (source) or continue destination inspection"
    if state == StorageLifecycleState.ATTACHED_UNVALIDATED:
        return "Validate Mercury HDD"
    if role == MigrationHostRole.DESTINATION_REHEARSAL and state != StorageLifecycleState.ATTACHED_WRITER_ENABLED:
        return "Inspect Mercury HDD read-only"
    if state == StorageLifecycleState.ATTACHED_WRITER_ENABLED:
        return "Run or verify backups"
    if state == StorageLifecycleState.ATTACHED_WRITER_DISABLED:
        return "Safe disconnect Mercury HDD" if package_verified else "Enable Mercury writes or continue preparation"
    return f"Open {main_menu_hint(ACTION_HDD_STORAGE)}"


def writes_disabled_redirect_message() -> str:
    """Concise refusal when a write action is selected while HDD writes are off."""
    from mercury.menu.options import MAIN_STORAGE, main_menu_hint
    from mercury.storage.hdd_menu_options import STORAGE_CHANGE_MODE, hdd_menu_hint

    main_hint = main_menu_hint(MAIN_STORAGE)
    mode_hint = hdd_menu_hint(STORAGE_CHANGE_MODE)
    return (
        "Operation unavailable\n\n"
        "Mercury writes are disabled while the HDD is being prepared for disconnect "
        "or destination rehearsal.\n\n"
        f"Next:\n  {main_hint} → {mode_hint}"
    )


def dashboard_hdd_line(snapshot: StorageLifecycleSnapshot | None = None) -> str:
    snap = snapshot or assess_storage_lifecycle(probe_disconnect=True)
    return snap.label


def dashboard_next_action_line(snapshot: StorageLifecycleSnapshot | None = None) -> str:
    snap = snapshot or assess_storage_lifecycle(probe_disconnect=True)
    return snap.recommended


def software_only_startup_needed(host: HostMaintenanceState | None = None) -> bool:
    """True when no operator config / never configured storage (observe-only heuristic)."""
    from mercury.core.paths import resolve_local_config

    if not resolve_local_config().exists():
        return True
    return False


def render_storage_first_run_prompt() -> str:
    """First-run storage choices — never formats or initializes a disk automatically."""
    return (
        "Mercury storage has not been configured.\n\n"
        "  [1] Configure an existing Mercury HDD\n"
        "  [2] Initialize a new Mercury HDD  (guarded — not available in this build)\n"
        "  [3] Start in software-only mode\n"
    )


def maybe_prompt_storage_first_run(*, interactive: bool) -> str | None:
    """Return chosen mode for first-run; None when storage already configured.

    Modes: ``configure_existing``, ``software_only``. Never initializes disks.
    Non-TTY / non-interactive sessions skip the prompt so scripted menus are not stolen.
    """
    if not software_only_startup_needed():
        return None
    from mercury import output
    from mercury.menu import prompts as menu_prompts
    from mercury.menu.options import ACTION_HDD_STORAGE, main_menu_hint
    import sys

    if not interactive or not sys.stdin.isatty():
        return "software_only"

    output.write(render_storage_first_run_prompt())
    while True:
        choice = menu_prompts.ask("Choice: ").strip()
        if choice == "1":
            output.write(
                f"Attach the HDD physically, then open {main_menu_hint(ACTION_HDD_STORAGE)} "
                "→ Reconnect or validate attached HDD."
            )
            return "configure_existing"
        if choice == "2":
            output.write(
                "Initialize a new Mercury HDD is a separate, highly guarded workflow "
                "and is not available in this build. Choose [1] or [3]."
            )
            continue
        if choice in {"3", ""}:
            output.write("Starting in software-only mode (no Mercury HDD writes).")
            return "software_only"
        output.write("Enter 1, 2, or 3.")


def ensure_no_mountpoint_mkdir(mount: Path | str) -> None:
    """Guard: refuse creating operator mount directories on NVMe when HDD absent."""
    path = Path(mount)
    if path.exists():
        return
    raise OSError(
        f"Refusing to create Mercury mount path {path} while the HDD is absent. "
        "Attach the Mercury HDD, then choose Reconnect."
    )
