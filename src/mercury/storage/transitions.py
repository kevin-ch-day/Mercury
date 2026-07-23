"""Centralized Mercury HDD storage-state transitions (host-maintenance owner)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import uuid
from typing import Any, Callable

from mercury.core.storage_roles import (
    DEFAULT_FILESYSTEM_TYPE,
    DEFAULT_PRIMARY_LABEL,
    DEFAULT_PRIMARY_MOUNT,
    DEFAULT_PRIMARY_UUID,
)
from mercury.storage.host_maintenance import (
    ENV_TEST_ISOLATION,
    HostMaintenanceState,
    assert_not_live_mercury_path,
    load_host_maintenance,
    save_host_maintenance,
)

ENV_TRANSITION_LEDGER = "MERCURY_TRANSITION_LEDGER_PATH"
ENV_ACTIVE_OPERATION = "MERCURY_ACTIVE_OPERATION"
ENV_LOCK_DIR = "MERCURY_OPERATION_LOCK_DIR"
ENV_EVENT_ENVIRONMENT = "MERCURY_EVENT_ENVIRONMENT"

LEDGER_SCHEMA_VERSION = 1

RESTORE_SOURCE_WRITER_PHRASE = "RESTORE SOURCE WRITER"
RESTORE_MERCURY_WRITES_PHRASE = "RESTORE MERCURY WRITES"


class TransitionName(str, Enum):
    RESTORE_SOURCE_WRITER = "restore_source_writer"
    CANCEL_DISCONNECT_PREPARATION = "cancel_disconnect_preparation"
    DISABLE_WRITES = "disable_writes"
    PREPARE_DISCONNECT = "prepare_disconnect"
    ENTER_READ_ONLY_INSPECTION = "enter_read_only_inspection"
    ENTER_DESTINATION_REHEARSAL = "enter_destination_rehearsal"
    RETURN_TO_SOURCE_OPERATION = "return_to_source_operation"


class TransitionStatus(str, Enum):
    """Outcome of a storage-state transition attempt."""

    SUCCESS = "SUCCESS"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    ALREADY_SATISFIED = "ALREADY_SATISFIED"
    HARD_BLOCK = "HARD_BLOCK"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


# Backward-compatible alias used by older call sites / tests.
class TransitionClassification(str, Enum):
    RECOVERABLE_CONFIRMATION = "RECOVERABLE_CONFIRMATION"
    STRONG_CONFIRMATION = "STRONG_CONFIRMATION"
    HARD_BLOCK = "HARD_BLOCK"
    ALREADY_SATISFIED = "ALREADY_SATISFIED"
    SUCCESS = "SUCCESS"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True)
class TransitionCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class TransitionResult:
    transition: str
    previous_state: dict[str, Any]
    resulting_state: dict[str, Any]
    status: TransitionStatus
    allowed: bool
    confirmation_required: bool = False
    confirmation_phrase: str = ""
    confirmation_class: str = ""
    checks: list[TransitionCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    rollback_information: str = ""
    continued_operation_allowed: bool = False
    messages: list[str] = field(default_factory=list)
    transition_id: str = ""
    package_id: str = ""
    source_delta: dict[str, Any] = field(default_factory=dict)
    backup_continued_afterward: bool | None = None

    @property
    def classification(self) -> TransitionStatus:
        """Alias for status (older call sites)."""
        return self.status

    @property
    def ok(self) -> bool:
        return self.status in {
            TransitionStatus.SUCCESS,
            TransitionStatus.ALREADY_SATISFIED,
        }


@dataclass(frozen=True)
class StorageFacts:
    """Independent storage facts — do not infer mounted from connected, etc."""

    device_connected: bool
    filesystem_mounted: bool
    mount_mode: str  # read-only | read-write | unknown | not_mounted
    filesystem_uuid_valid: bool
    filesystem_type_valid: bool
    expected_mountpoint: bool
    writes_allowed: bool
    active_write_role: str
    storage_availability: str
    destination_rehearsal_in_progress: bool
    destination_validation_state: str
    active_operation: str
    package_state: str
    device_uuid: str = ""
    device_label: str = ""
    mountpoint: str = ""
    filesystem: str = ""
    desktop_automount: bool = False
    identity_mismatch: bool = False
    destination_rehearsal_active: bool = False
    destination_rehearsal_planned: bool = False
    source_detach_preparation: bool = False
    source_writes_resumed_after_package: bool = False

    @property
    def compact_line(self) -> str:
        if not self.device_connected:
            return "Not connected"
        if not self.filesystem_mounted:
            return "Connected · not mounted"
        if self.mount_mode == "read-only":
            return "Connected · mounted read-only"
        if self.writes_allowed and self.active_write_role == "primary":
            return "Connected · mounted · backups enabled"
        return "Connected · mounted · writes disabled"


def default_transition_ledger_path() -> Path:
    override = os.environ.get(ENV_TRANSITION_LEDGER)
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "mercury" / "transition_ledger.jsonl"


def _state_as_dict(state: HostMaintenanceState) -> dict[str, Any]:
    return asdict(state)


def _mercury_commit() -> str:
    env = (os.environ.get("MERCURY_COMMIT") or "").strip()
    if env:
        return env
    try:
        import subprocess

        from mercury.core.paths import REPO_ROOT

        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return (completed.stdout or "").strip()
    except OSError:
        pass
    return "unknown"


def _event_environment() -> str:
    override = (os.environ.get(ENV_EVENT_ENVIRONMENT) or "").strip().lower()
    if override in {"test", "preview", "live"}:
        return override
    if os.environ.get(ENV_TEST_ISOLATION, "").strip() in {"1", "true", "yes"}:
        return "test"
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "test"
    return "live"


def new_transition_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:12]}"


def append_transition_ledger(
    record: dict[str, Any],
    *,
    path: Path | None = None,
) -> Path:
    """Append host-local transition audit (never under the Mercury HDD)."""
    path = path or default_transition_ledger_path()
    assert_not_live_mercury_path(path, purpose="transition_ledger write")
    if path_is_under_forbidden_hdd(path):
        raise RuntimeError("Refusing to write transition ledger under Mercury HDD")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **record,
        "schema_version": LEDGER_SCHEMA_VERSION,
        "transition_id": record.get("transition_id") or new_transition_id(),
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mercury_commit": _mercury_commit(),
        "event_environment": _event_environment(),
        "evidence_class": "host_local_transition",
        "not_hdd_evidence": True,
        "governed_hdd_backup_evidence": False,
    }
    # Safe append: serialize then O_APPEND write of one JSONL line.
    line = json.dumps(payload, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def path_is_under_forbidden_hdd(path: Path | str) -> bool:
    from mercury.storage.host_maintenance import path_is_under_primary_mount

    return path_is_under_primary_mount(path)


def detect_active_operations() -> list[str]:
    """Return names of operations that must hard-block storage transitions."""
    found: list[str] = []
    env_op = (os.environ.get(ENV_ACTIVE_OPERATION) or "").strip()
    if env_op:
        found.append(env_op)
    lock_root = Path(
        (os.environ.get(ENV_LOCK_DIR) or "").strip()
        or str(Path.home() / ".local" / "share" / "mercury" / "locks")
    )
    # Under test isolation, never scan the live lock directory.
    if os.environ.get(ENV_TEST_ISOLATION, "").strip() in {"1", "true", "yes"}:
        live_locks = (Path.home() / ".local" / "share" / "mercury" / "locks").resolve()
        try:
            if lock_root.expanduser().resolve() == live_locks:
                return found
        except OSError:
            pass
    if lock_root.is_dir():
        for lock in sorted(lock_root.glob("*.lock")):
            found.append(lock.stem)
    return found


def observe_storage_facts(
    *,
    host: HostMaintenanceState | None = None,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    expected_mount: str = DEFAULT_PRIMARY_MOUNT,
    expected_fstype: str = DEFAULT_FILESYSTEM_TYPE,
    resolve_fn: Callable[..., Any] | None = None,
) -> StorageFacts:
    """Observe device + host-maintenance facts without mutating state."""
    from mercury.storage.block_device import resolve_mercury_block_device
    from mercury.storage.detach_wizard import detect_desktop_automount
    from mercury.storage.host_maintenance import writes_allowed as host_writes_allowed

    state = host or load_host_maintenance()
    active_ops = detect_active_operations()
    device_connected = False
    filesystem_mounted = False
    mount_mode = "not_mounted"
    uuid_ok = False
    fstype_ok = False
    expected_mp = False
    device_uuid = ""
    device_label = ""
    mountpoint = ""
    filesystem = ""
    identity_mismatch = False
    desktop = False

    try:
        desktop = bool(detect_desktop_automount(DEFAULT_PRIMARY_LABEL))
    except OSError:
        desktop = False

    resolve = resolve_fn or resolve_mercury_block_device
    try:
        resolved = resolve(require_mounted=False, expected_uuid=expected_uuid)
    except OSError:
        resolved = None

    if resolved is not None and getattr(resolved, "identity", None) is not None:
        ident = resolved.identity
        device_connected = True
        device_uuid = ident.uuid or ""
        device_label = ident.label or ""
        mountpoint = ident.mountpoint or ""
        filesystem = (ident.fstype or "").lower()
        uuid_ok = bool(device_uuid) and device_uuid == expected_uuid
        fstype_ok = filesystem == expected_fstype.lower()
        filesystem_mounted = bool(mountpoint)
        if mountpoint:
            expected_mp = Path(mountpoint) == Path(expected_mount)
            if str(mountpoint).startswith("/run/media"):
                expected_mp = False
            mount_mode = _probe_mount_mode(mountpoint)
        joined_err = " ".join(getattr(resolved, "errors", []) or []).lower()
        if "mismatch" in joined_err or "wrong" in joined_err:
            identity_mismatch = True
        if expected_uuid and device_uuid and device_uuid != expected_uuid:
            identity_mismatch = True
            uuid_ok = False
    elif resolved is not None and getattr(resolved, "errors", None):
        joined = " ".join(resolved.errors).lower()
        if "mismatch" in joined or "wrong" in joined:
            identity_mismatch = True

    package_state = state.package_verification_status or "unknown"
    dest_validation = (
        "verified"
        if state.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
        else "pending"
    )

    return StorageFacts(
        device_connected=device_connected,
        filesystem_mounted=filesystem_mounted,
        mount_mode=mount_mode,
        filesystem_uuid_valid=uuid_ok,
        filesystem_type_valid=fstype_ok,
        expected_mountpoint=expected_mp,
        writes_allowed=host_writes_allowed(state),
        active_write_role=state.active_write_role or "none",
        storage_availability=state.storage_availability,
        destination_rehearsal_in_progress=bool(state.destination_rehearsal_in_progress),
        destination_validation_state=dest_validation,
        active_operation=",".join(active_ops) if active_ops else "",
        package_state=package_state,
        device_uuid=device_uuid,
        device_label=device_label,
        mountpoint=mountpoint,
        filesystem=filesystem,
        desktop_automount=desktop,
        identity_mismatch=identity_mismatch,
        destination_rehearsal_active=bool(state.destination_rehearsal_active),
        destination_rehearsal_planned=bool(state.destination_rehearsal_planned),
        source_detach_preparation=bool(state.source_detach_preparation),
        source_writes_resumed_after_package=bool(
            state.source_writes_resumed_after_package
        ),
    )


def _probe_mount_mode(mountpoint: str) -> str:
    import subprocess

    try:
        completed = subprocess.run(
            ["findmnt", "-n", "-o", "OPTIONS", mountpoint],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    opts = (completed.stdout or "").strip()
    if not opts:
        return "unknown"
    parts = {p.strip() for p in opts.split(",")}
    if "ro" in parts:
        return "read-only"
    if "rw" in parts:
        return "read-write"
    return "unknown"


def _atomic_replace_state(
    previous: HostMaintenanceState,
    proposed: HostMaintenanceState,
    *,
    path: Path | None,
    validate_fn: Callable[[HostMaintenanceState], list[str]] | None,
    save_fn: Callable[..., Path] | None = None,
) -> tuple[HostMaintenanceState | None, list[str], str]:
    """Write proposed state; roll back to previous on validation failure."""
    writer = save_fn or save_host_maintenance
    writer(proposed, path=path)
    errors: list[str] = []
    if validate_fn is not None:
        errors = list(validate_fn(proposed))
    if errors:
        writer(previous, path=path)
        return None, errors, "rolled_back_to_previous_host_maintenance"
    return load_host_maintenance(path), [], ""


def _post_restore_validate(state: HostMaintenanceState) -> list[str]:
    errors: list[str] = []
    if state.storage_availability not in {"mounted", "attached"}:
        errors.append(f"unexpected storage_availability={state.storage_availability}")
    if not state.writes_allowed:
        errors.append("writes_allowed remained false after restore")
    if state.active_write_role != "primary":
        errors.append(f"active_write_role={state.active_write_role!r}, expected primary")
    if state.source_detach_preparation:
        errors.append("source_detach_preparation remained true after restore")
    return errors


def _source_delta_for_restore(previous: HostMaintenanceState) -> dict[str, Any]:
    verified = previous.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
    has_package = bool(previous.package_id) and verified
    if not has_package:
        return {}
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "source_writes_resumed_after_package": True,
        "source_writes_resumed_at": started,
        "source_delta_started_at": started,
        "source_delta_relative_to_package_id": previous.package_id,
        "source_delta_reason": "operator_restored_source_writer",
        "recovery_artifacts_created_after_package": False,
        "first_post_package_artifact_at": "",
        "first_post_package_artifact_type": "",
        "first_post_package_artifact_id": "",
        "source_data_changed_since_package": False,
        "source_data_first_change_at": "",
        "source_data_first_change_operation": "",
        "development_state_changed_since_package": False,
        "development_state_first_change_at": "",
        "development_state_first_change_operation": "",
        "source_changed_since_package": False,
        "source_delta_first_write_at": "",
        "source_delta_first_write_operation": "",
        "source_delta_first_artifact_id": "",
    }


def _package_delta_kwargs(state: HostMaintenanceState) -> dict[str, Any]:
    """Preserve post-package delta fields across unrelated transitions."""
    return {
        "source_writes_resumed_after_package": state.source_writes_resumed_after_package,
        "source_writes_resumed_at": getattr(state, "source_writes_resumed_at", "")
        or state.source_delta_started_at,
        "source_delta_started_at": state.source_delta_started_at,
        "source_delta_relative_to_package_id": state.source_delta_relative_to_package_id,
        "source_delta_reason": state.source_delta_reason,
        "recovery_artifacts_created_after_package": getattr(
            state, "recovery_artifacts_created_after_package", False
        ),
        "first_post_package_artifact_at": getattr(
            state, "first_post_package_artifact_at", ""
        ),
        "first_post_package_artifact_type": getattr(
            state, "first_post_package_artifact_type", ""
        ),
        "first_post_package_artifact_id": getattr(
            state, "first_post_package_artifact_id", ""
        ),
        "source_data_changed_since_package": getattr(
            state, "source_data_changed_since_package", False
        ),
        "source_data_first_change_at": getattr(state, "source_data_first_change_at", ""),
        "source_data_first_change_operation": getattr(
            state, "source_data_first_change_operation", ""
        ),
        "development_state_changed_since_package": getattr(
            state, "development_state_changed_since_package", False
        ),
        "development_state_first_change_at": getattr(
            state, "development_state_first_change_at", ""
        ),
        "development_state_first_change_operation": getattr(
            state, "development_state_first_change_operation", ""
        ),
        "source_changed_since_package": state.source_changed_since_package,
        "source_delta_first_write_at": getattr(state, "source_delta_first_write_at", ""),
        "source_delta_first_write_operation": getattr(
            state, "source_delta_first_write_operation", ""
        ),
        "source_delta_first_artifact_id": getattr(
            state, "source_delta_first_artifact_id", ""
        ),
    }


def restore_source_writer(
    *,
    confirm: str | None = None,
    operator_intent: str = "restore_source_writer",
    path: Path | None = None,
    require_strong_phrase: bool = False,
    facts: StorageFacts | None = None,
    resolve_fn: Callable[..., Any] | None = None,
    skip_device_checks: bool = False,
    save_fn: Callable[..., Path] | None = None,
    ledger_path: Path | None = None,
    ledger_fn: Callable[..., Path] | None = None,
    confirmation_class: str = "",
) -> TransitionResult:
    """Cancel disconnect prep and restore primary source writer (atomic)."""
    previous = load_host_maintenance(path)
    prev_dict = _state_as_dict(previous)
    transition = TransitionName.RESTORE_SOURCE_WRITER.value
    transition_id = new_transition_id()
    observed = facts or observe_storage_facts(host=previous, resolve_fn=resolve_fn)
    ledger = ledger_fn or append_transition_ledger

    if previous.writes_allowed and previous.storage_availability not in {
        "detaching",
        "detached",
    } and not previous.source_detach_preparation:
        return TransitionResult(
            transition=transition,
            previous_state=prev_dict,
            resulting_state=prev_dict,
            status=TransitionStatus.ALREADY_SATISFIED,
            allowed=True,
            continued_operation_allowed=True,
            messages=["Source writer already enabled."],
            transition_id=transition_id,
            package_id=previous.package_id,
        )

    blockers: list[str] = []
    checks: list[TransitionCheck] = []

    if not skip_device_checks:
        checks.append(
            TransitionCheck("device_connected", observed.device_connected, observed.device_uuid or "—")
        )
        checks.append(
            TransitionCheck(
                "filesystem_uuid_valid",
                observed.filesystem_uuid_valid,
                observed.device_uuid or "missing",
            )
        )
        checks.append(
            TransitionCheck(
                "filesystem_type_valid",
                observed.filesystem_type_valid,
                observed.filesystem or "missing",
            )
        )
        checks.append(
            TransitionCheck(
                "expected_mountpoint",
                observed.expected_mountpoint,
                observed.mountpoint or "not mounted",
            )
        )
        checks.append(
            TransitionCheck(
                "not_desktop_automount",
                not observed.desktop_automount,
                "desktop automount" if observed.desktop_automount else "ok",
            )
        )
        if not observed.device_connected:
            blockers.append("Mercury HDD is absent")
        if observed.identity_mismatch or not observed.filesystem_uuid_valid:
            blockers.append("Device UUID does not match the approved Mercury HDD")
        if observed.filesystem_mounted and not observed.expected_mountpoint:
            blockers.append(
                f"Mountpoint is not the contract path ({DEFAULT_PRIMARY_MOUNT})"
            )
        if observed.desktop_automount:
            blockers.append("Desktop automount under /run/media is not allowed")
        if observed.filesystem_mounted and not observed.filesystem_type_valid:
            blockers.append("Filesystem type is not ext4")
        if observed.mount_mode == "read-only":
            blockers.append("Filesystem is mounted read-only")

    if observed.active_operation:
        for name in observed.active_operation.split(","):
            blockers.append(f"Active operation in progress: {name}")

    if blockers:
        result = TransitionResult(
            transition=transition,
            previous_state=prev_dict,
            resulting_state=prev_dict,
            status=TransitionStatus.HARD_BLOCK,
            allowed=False,
            checks=checks,
            blockers=blockers,
            continued_operation_allowed=False,
            transition_id=transition_id,
            package_id=previous.package_id,
            confirmation_class=confirmation_class,
        )
        try:
            ledger(
                {
                    "transition": transition,
                    "transition_id": transition_id,
                    "previous_state": prev_dict,
                    "result": result.status.value,
                    "operator_intent": operator_intent,
                    "confirmation_class": confirmation_class,
                    "package_id": previous.package_id,
                    "blockers": blockers,
                    "backup_continued_afterward": None,
                },
                path=ledger_path,
            )
        except OSError:
            pass
        return result

    if require_strong_phrase:
        if confirm not in {RESTORE_SOURCE_WRITER_PHRASE, RESTORE_MERCURY_WRITES_PHRASE}:
            return TransitionResult(
                transition=transition,
                previous_state=prev_dict,
                resulting_state=prev_dict,
                status=TransitionStatus.CONFIRMATION_REQUIRED,
                allowed=False,
                confirmation_required=True,
                confirmation_phrase=RESTORE_SOURCE_WRITER_PHRASE,
                confirmation_class=confirmation_class or "STRONG_CONFIRMATION",
                checks=checks,
                warnings=[
                    "Restoring source writes may create changes that are not included in the "
                    "current destination rehearsal package.",
                    "The existing Phase 3B package will remain valid for rehearsal, but it will "
                    "not represent the newest source state.",
                ],
                continued_operation_allowed=False,
                transition_id=transition_id,
                package_id=previous.package_id,
            )

    delta = _source_delta_for_restore(previous)
    proposed = HostMaintenanceState(
        storage_availability="mounted",
        writes_allowed=True,
        active_write_role="primary",
        source_detach_preparation=False,
        destination_rehearsal_active=False,
        destination_rehearsal_in_progress=False,
        destination_rehearsal_planned=bool(
            previous.destination_rehearsal_planned
            or previous.package_id
            or previous.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
        ),
        package_id=previous.package_id,
        package_verification_status=previous.package_verification_status,
        notes=(
            f"Operator restored source writer ({operator_intent}); "
            "destination cutover is NOT complete; Phase 3B package unchanged; "
            "subsequent source writes are outside the verified rehearsal snapshot."
            if delta
            else (
                f"Operator restored source writer ({operator_intent}); "
                "destination cutover is NOT complete; Phase 3B unchanged."
            )
        ),
        source_writes_resumed_after_package=bool(
            delta.get("source_writes_resumed_after_package", False)
        ),
        source_writes_resumed_at=str(delta.get("source_writes_resumed_at") or ""),
        source_delta_started_at=str(delta.get("source_delta_started_at") or ""),
        source_delta_relative_to_package_id=str(
            delta.get("source_delta_relative_to_package_id") or ""
        ),
        source_delta_reason=str(delta.get("source_delta_reason") or ""),
        recovery_artifacts_created_after_package=False,
        source_data_changed_since_package=False,
        development_state_changed_since_package=False,
        source_changed_since_package=False,
    )
    try:
        restored, errors, rollback = _atomic_replace_state(
            previous,
            proposed,
            path=path,
            validate_fn=_post_restore_validate,
            save_fn=save_fn,
        )
    except OSError as exc:
        # Device/state write failed before a durable success — ensure previous remains.
        try:
            (save_fn or save_host_maintenance)(previous, path=path)
        except OSError:
            pass
        result = TransitionResult(
            transition=transition,
            previous_state=prev_dict,
            resulting_state=prev_dict,
            status=TransitionStatus.FAILED,
            allowed=False,
            checks=checks,
            blockers=[f"atomic state write failed: {exc}"],
            rollback_information="restored_previous_after_write_failure",
            continued_operation_allowed=False,
            transition_id=transition_id,
            package_id=previous.package_id,
            confirmation_class=confirmation_class,
        )
        try:
            ledger(
                {
                    "transition": transition,
                    "transition_id": transition_id,
                    "previous_state": prev_dict,
                    "result": result.status.value,
                    "operator_intent": operator_intent,
                    "confirmation_class": confirmation_class,
                    "package_id": previous.package_id,
                    "blockers": result.blockers,
                    "backup_continued_afterward": False,
                },
                path=ledger_path,
            )
        except OSError:
            pass
        return result

    if restored is None:
        result = TransitionResult(
            transition=transition,
            previous_state=prev_dict,
            resulting_state=prev_dict,
            status=TransitionStatus.ROLLED_BACK,
            allowed=False,
            checks=checks,
            blockers=errors,
            rollback_information=rollback,
            continued_operation_allowed=False,
            transition_id=transition_id,
            package_id=previous.package_id,
            confirmation_class=confirmation_class,
            source_delta=delta,
        )
        try:
            ledger(
                {
                    "transition": transition,
                    "transition_id": transition_id,
                    "previous_state": prev_dict,
                    "result": result.status.value,
                    "operator_intent": operator_intent,
                    "confirmation_class": confirmation_class,
                    "package_id": previous.package_id,
                    "blockers": errors,
                    "rollback": rollback,
                    "source_delta": delta,
                    "backup_continued_afterward": False,
                },
                path=ledger_path,
            )
        except OSError:
            pass
        return result

    result = TransitionResult(
        transition=transition,
        previous_state=prev_dict,
        resulting_state=_state_as_dict(restored),
        status=TransitionStatus.SUCCESS,
        allowed=True,
        checks=checks,
        continued_operation_allowed=True,
        messages=["Source backup writer restored."],
        transition_id=transition_id,
        package_id=restored.package_id,
        confirmation_class=confirmation_class
        or ("STRONG_CONFIRMATION" if require_strong_phrase else "RECOVERABLE_CONFIRMATION"),
        source_delta=delta,
        backup_continued_afterward=None,
    )
    try:
        ledger(
            {
                "transition": transition,
                "transition_id": transition_id,
                "previous_state": prev_dict,
                "resulting_state": result.resulting_state,
                "result": result.status.value,
                "operator_intent": operator_intent,
                "confirmation_class": result.confirmation_class,
                "transition_reason": "cancel_disconnect_preparation_and_restore_writer",
                "package_id": restored.package_id,
                "source_delta": delta,
                "backup_continued_afterward": None,
            },
            path=ledger_path,
        )
    except OSError as exc:
        result.warnings.append(f"transition ledger write failed: {exc}")
        # Writer state remains restored; ledger failure does not roll back.
    return result


def record_backup_continuation(
    *,
    transition_id: str,
    backup_ran: bool,
    backup_succeeded: bool | None = None,
    path: Path | None = None,
) -> Path:
    """Append a follow-up ledger row linking backup outcome to a prior transition."""
    return append_transition_ledger(
        {
            "transition": "backup_continuation",
            "transition_id": new_transition_id(),
            "related_transition_id": transition_id,
            "result": "RECORDED",
            "backup_continued_afterward": backup_ran,
            "backup_succeeded": backup_succeeded,
            "operator_intent": "record_backup_continuation",
        },
        path=path,
    )


def cancel_disconnect_preparation(
    *,
    confirm: str | None = None,
    operator_intent: str = "cancel_disconnect_preparation",
    path: Path | None = None,
    require_strong_phrase: bool = False,
    facts: StorageFacts | None = None,
    resolve_fn: Callable[..., Any] | None = None,
) -> TransitionResult:
    """Alias path: cancel detach prep by restoring the source writer."""
    return restore_source_writer(
        confirm=confirm,
        operator_intent=operator_intent,
        path=path,
        require_strong_phrase=require_strong_phrase,
        facts=facts,
        resolve_fn=resolve_fn,
    )


def disable_writes(
    *,
    operator_intent: str = "disable_writes",
    path: Path | None = None,
) -> TransitionResult:
    previous = load_host_maintenance(path)
    prev_dict = _state_as_dict(previous)
    if not previous.writes_allowed and previous.active_write_role in {"none", ""}:
        return TransitionResult(
            transition=TransitionName.DISABLE_WRITES.value,
            previous_state=prev_dict,
            resulting_state=prev_dict,
            status=TransitionStatus.ALREADY_SATISFIED,
            allowed=True,
            continued_operation_allowed=False,
            messages=["Mercury writes already disabled."],
        )
    proposed = HostMaintenanceState(
        storage_availability=previous.storage_availability
        if previous.storage_availability != "detached"
        else "detached",
        writes_allowed=False,
        active_write_role="none",
        source_detach_preparation=previous.source_detach_preparation,
        destination_rehearsal_active=previous.destination_rehearsal_active,
        destination_rehearsal_planned=previous.destination_rehearsal_planned,
        destination_rehearsal_in_progress=previous.destination_rehearsal_active,
        package_id=previous.package_id,
        package_verification_status=previous.package_verification_status,
        notes=f"Operator disabled Mercury writes ({operator_intent}).",
        **_package_delta_kwargs(previous),
    )
    save_host_maintenance(proposed, path=path)
    current = load_host_maintenance(path)
    result = TransitionResult(
        transition=TransitionName.DISABLE_WRITES.value,
        previous_state=prev_dict,
        resulting_state=_state_as_dict(current),
        status=TransitionStatus.SUCCESS,
        allowed=True,
        continued_operation_allowed=False,
        messages=["Mercury writes disabled."],
        transition_id=new_transition_id(),
        package_id=current.package_id,
    )
    append_transition_ledger(
        {
            "transition": result.transition,
            "transition_id": result.transition_id,
            "previous_state": prev_dict,
            "resulting_state": result.resulting_state,
            "result": result.status.value,
            "operator_intent": operator_intent,
            "package_id": current.package_id,
        }
    )
    return result


def prepare_disconnect(
    *,
    operator_intent: str = "prepare_disconnect",
    path: Path | None = None,
) -> TransitionResult:
    previous = load_host_maintenance(path)
    prev_dict = _state_as_dict(previous)
    proposed = HostMaintenanceState(
        storage_availability="detaching",
        writes_allowed=False,
        active_write_role="none",
        source_detach_preparation=True,
        destination_rehearsal_active=previous.destination_rehearsal_active,
        destination_rehearsal_planned=previous.destination_rehearsal_planned
        or bool(previous.package_id),
        destination_rehearsal_in_progress=previous.destination_rehearsal_active,
        package_id=previous.package_id,
        package_verification_status=previous.package_verification_status,
        notes=f"Preparing for safe disconnect ({operator_intent}).",
        **_package_delta_kwargs(previous),
    )
    save_host_maintenance(proposed, path=path)
    current = load_host_maintenance(path)
    result = TransitionResult(
        transition=TransitionName.PREPARE_DISCONNECT.value,
        previous_state=prev_dict,
        resulting_state=_state_as_dict(current),
        status=TransitionStatus.SUCCESS,
        allowed=True,
        continued_operation_allowed=False,
        messages=["Disconnect preparation active; writes disabled."],
        transition_id=new_transition_id(),
        package_id=current.package_id,
    )
    append_transition_ledger(
        {
            "transition": result.transition,
            "transition_id": result.transition_id,
            "previous_state": prev_dict,
            "resulting_state": result.resulting_state,
            "result": result.status.value,
            "operator_intent": operator_intent,
            "package_id": current.package_id,
        }
    )
    return result


def enter_read_only_inspection(
    *,
    operator_intent: str = "enter_read_only_inspection",
    path: Path | None = None,
) -> TransitionResult:
    previous = load_host_maintenance(path)
    prev_dict = _state_as_dict(previous)
    proposed = HostMaintenanceState(
        storage_availability="attached",
        writes_allowed=False,
        active_write_role="none",
        source_detach_preparation=False,
        destination_rehearsal_active=previous.destination_rehearsal_active,
        destination_rehearsal_planned=previous.destination_rehearsal_planned,
        destination_rehearsal_in_progress=previous.destination_rehearsal_active,
        package_id=previous.package_id,
        package_verification_status=previous.package_verification_status,
        notes=f"Read-only inspection mode ({operator_intent}).",
        **_package_delta_kwargs(previous),
    )
    save_host_maintenance(proposed, path=path)
    current = load_host_maintenance(path)
    result = TransitionResult(
        transition=TransitionName.ENTER_READ_ONLY_INSPECTION.value,
        previous_state=prev_dict,
        resulting_state=_state_as_dict(current),
        status=TransitionStatus.SUCCESS,
        allowed=True,
        continued_operation_allowed=False,
        messages=["Entered read-only inspection; writes remain disabled."],
        transition_id=new_transition_id(),
        package_id=current.package_id,
    )
    append_transition_ledger(
        {
            "transition": result.transition,
            "transition_id": result.transition_id,
            "previous_state": prev_dict,
            "resulting_state": result.resulting_state,
            "result": result.status.value,
            "operator_intent": operator_intent,
            "package_id": current.package_id,
        }
    )
    return result


def enter_destination_rehearsal(
    *,
    operator_intent: str = "enter_destination_rehearsal",
    path: Path | None = None,
) -> TransitionResult:
    previous = load_host_maintenance(path)
    prev_dict = _state_as_dict(previous)
    proposed = HostMaintenanceState(
        storage_availability="attached",
        writes_allowed=False,
        active_write_role="none",
        source_detach_preparation=False,
        destination_rehearsal_active=True,
        destination_rehearsal_planned=True,
        destination_rehearsal_in_progress=True,
        package_id=previous.package_id,
        package_verification_status=previous.package_verification_status,
        notes=f"Destination rehearsal ({operator_intent}); writes remain disabled.",
        **_package_delta_kwargs(previous),
    )
    save_host_maintenance(proposed, path=path)
    current = load_host_maintenance(path)
    result = TransitionResult(
        transition=TransitionName.ENTER_DESTINATION_REHEARSAL.value,
        previous_state=prev_dict,
        resulting_state=_state_as_dict(current),
        status=TransitionStatus.SUCCESS,
        allowed=True,
        continued_operation_allowed=False,
        messages=["Destination rehearsal flagged; writes remain disabled."],
        transition_id=new_transition_id(),
        package_id=current.package_id,
    )
    append_transition_ledger(
        {
            "transition": result.transition,
            "transition_id": result.transition_id,
            "previous_state": prev_dict,
            "resulting_state": result.resulting_state,
            "result": result.status.value,
            "operator_intent": operator_intent,
            "package_id": current.package_id,
        }
    )
    return result


def return_to_source_operation(
    *,
    confirm: str | None = None,
    operator_intent: str = "return_to_source_operation",
    path: Path | None = None,
    require_strong_phrase: bool = True,
    facts: StorageFacts | None = None,
    resolve_fn: Callable[..., Any] | None = None,
) -> TransitionResult:
    return restore_source_writer(
        confirm=confirm,
        operator_intent=operator_intent,
        path=path,
        require_strong_phrase=require_strong_phrase,
        facts=facts,
        resolve_fn=resolve_fn,
        confirmation_class="STRONG_CONFIRMATION",
    )
