"""Reconnect / validate Mercury HDD by UUID (source or destination)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import subprocess

from mercury.core.storage_roles import (
    DEFAULT_PRIMARY_LABEL,
    DEFAULT_PRIMARY_MOUNT,
    DEFAULT_PRIMARY_UUID,
)
from mercury.storage.block_device import (
    resolve_mercury_block_device,
    systemd_mount_unit_for_path,
)
from mercury.storage.detach_wizard import detect_desktop_automount
from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    load_host_maintenance,
    save_host_maintenance,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class ReconnectResult:
    ok: bool
    mode: str  # source_rw_pending | destination_read_only | inspect_only
    result_state: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    identity: dict[str, Any] | None = None
    commands_invoked: list[list[str]] = field(default_factory=list)


def mark_attached_pending_confirm(*, path: Path | None = None) -> HostMaintenanceState:
    """Record that the HDD is present again; writes stay disabled until explicit confirm."""
    state = load_host_maintenance(path)
    state.storage_availability = "attached"
    state.writes_allowed = False
    state.active_write_role = "none"
    state.destination_rehearsal_in_progress = True
    state.notes = (
        "Mercury HDD reconnected; writes remain disabled until explicit operator confirmation. "
        "Destination cutover is NOT complete."
    )
    save_host_maintenance(state, path=path)
    return state


def restore_writes_after_reconnect(
    *,
    confirm: str,
    path: Path | None = None,
) -> HostMaintenanceState | None:
    if confirm != "RESTORE MERCURY WRITES":
        return None
    state = load_host_maintenance(path)
    state.storage_availability = "attached"
    state.writes_allowed = True
    state.active_write_role = "primary"
    state.destination_rehearsal_in_progress = False
    state.notes = "Operator restored Mercury writes after reconnect validation."
    save_host_maintenance(state, path=path)
    return state


def run_reconnect_validate(
    *,
    mode: str = "source",
    execute_mount: bool = False,
    read_only: bool = False,
    expected_uuid: str = DEFAULT_PRIMARY_UUID,
    mount: str = DEFAULT_PRIMARY_MOUNT,
    runner: Runner | None = None,
    privileged_runner: Runner | None = None,
    lsblk_json: dict[str, Any] | None = None,
) -> ReconnectResult:
    """Validate UUID identity and optionally mount.

    Destination hosts should use mode='destination' with read_only=True.
    Never enables writes automatically.
    """
    run = runner or (
        lambda argv, check=False, capture_output=True, text=True: subprocess.run(
            argv, check=check, capture_output=capture_output, text=text
        )
    )
    priv = privileged_runner or run
    result = ReconnectResult(
        ok=False,
        mode=(
            "destination_read_only"
            if mode == "destination" or read_only
            else "source_rw_pending"
        ),
        result_state="RECONNECT_BLOCKED",
    )

    automounts = detect_desktop_automount(DEFAULT_PRIMARY_LABEL)
    if automounts:
        result.errors.append(f"unexpected desktop automount: {automounts}")
        return result

    resolved = resolve_mercury_block_device(
        expected_uuid=expected_uuid,
        expected_mount=mount,
        require_mounted=False,
        runner=run,
        lsblk_json=lsblk_json,
    )
    if resolved.identity is None:
        result.errors.extend(resolved.errors or ["UUID not found"])
        result.result_state = "RECONNECT_UUID_NOT_FOUND"
        return result

    # If require_mounted was false, label/model checks may still have errors when unmounted
    identity = resolved.identity
    result.identity = identity.as_dict()
    if identity.label and identity.label != DEFAULT_PRIMARY_LABEL:
        result.errors.append(f"label mismatch: {identity.label}")
    if resolved.errors and identity.mountpoint:
        # Mounted but inconsistent
        result.errors.extend(resolved.errors)
        return result

    if identity.mountpoint and identity.mountpoint != mount:
        result.errors.append(
            f"mounted at unexpected path {identity.mountpoint} (want {mount})"
        )
        return result

    if execute_mount and not identity.mountpoint:
        unit = systemd_mount_unit_for_path(mount)
        if read_only or mode == "destination":
            argv = ["sudo", "mount", "-o", "ro", f"UUID={expected_uuid}", mount]
        else:
            argv = ["sudo", "systemctl", "start", unit]
        proc = priv(argv, check=False, capture_output=True, text=True)
        result.commands_invoked.append(argv)
        if proc.returncode != 0:
            result.errors.append(
                f"mount failed: {(proc.stderr or proc.stdout or '').strip()}"
            )
            result.result_state = "RECONNECT_MOUNT_FAILED"
            return result

    mark_attached_pending_confirm()
    result.ok = True
    result.result_state = (
        "RECONNECT_MOUNTED_READ_ONLY"
        if (read_only or mode == "destination")
        else "RECONNECT_VALIDATED_WRITES_DISABLED"
    )
    result.messages.append(
        "HDD identity validated by UUID. Writes remain disabled until explicit confirmation."
    )
    result.messages.append(
        "Next: ./run.sh storage validate  ·  restore writes only with --confirm 'RESTORE MERCURY WRITES'"
    )
    if mode == "destination":
        result.messages.append(
            "Destination mode: Inspect read-only → Validate package → Prepare rehearsal "
            "(do not enable writes automatically)."
        )
    return result
