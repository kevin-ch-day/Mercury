"""Mercury HDD primary menu — four context-aware actions (symbolic IDs)."""

from __future__ import annotations

from typing import Final

from mercury.storage.lifecycle import (
    LIFECYCLE_LABELS,
    MigrationHostRole,
    StorageLifecycleSnapshot,
    StorageLifecycleState,
)

# Primary menu action IDs (stable — do not hard-code numbers in hints/tests).
STORAGE_RECOMMENDED_ACTION = "storage_recommended_action"
STORAGE_STATUS_VALIDATE = "storage_status_validate"
STORAGE_CHANGE_MODE = "storage_change_mode"
STORAGE_MAINTENANCE = "storage_maintenance"
# Backward-compatible alias
STORAGE_CLEANUP_ADVANCED = STORAGE_MAINTENANCE

# Change-mode submenu IDs
MODE_INSPECT_RO = "mode_inspect_readonly"
MODE_RESTORE_WRITER = "mode_restore_source_writer"
MODE_DISABLE_WRITES = "mode_disable_writes"
MODE_PREPARE_DISCONNECT = "mode_prepare_disconnect"
MODE_KEEP_WRITES_DISABLED = "mode_keep_writes_disabled"
MODE_DESTINATION_REHEARSAL = "mode_destination_rehearsal"
MODE_CONTINUE_RO = "mode_continue_readonly"

# Cleanup / advanced submenu IDs
ADV_CLEANUP_STATUS = "adv_cleanup_status"
ADV_CLEANUP_PREVIEW = "adv_cleanup_preview"
ADV_DEVICE_DETAIL = "adv_device_detail"
ADV_SMART = "adv_smart_health"
ADV_ARCHIVE_USB = "adv_archive_usb"
ADV_TROUBLESHOOT = "adv_troubleshoot"

# Fixed primary slots (key, base label, action_id) — [1] label is state-dependent.
PRIMARY_FIXED: Final[list[tuple[str, str, str]]] = [
    ("2", "Storage status and validation", STORAGE_STATUS_VALIDATE),
    ("3", "Reconnect or change storage mode", STORAGE_CHANGE_MODE),
    ("4", "Cleanup and advanced tools", STORAGE_MAINTENANCE),
]


def hdd_menu_option_by_action(action_id: str) -> tuple[str, str]:
    """Resolve a primary menu key/label for a symbolic action id."""
    if action_id in {STORAGE_RECOMMENDED_ACTION}:
        return "1", "Recommended action"
    for key, label, action in PRIMARY_FIXED:
        if action == action_id:
            return key, label
    raise KeyError(f"Unknown HDD menu action: {action_id}")


def hdd_menu_hint(action_id: str) -> str:
    key, label = hdd_menu_option_by_action(action_id)
    return f"{label} [{key}]"


def recommended_primary_label(snapshot: StorageLifecycleSnapshot) -> tuple[str, str]:
    """Return ``(label, optional_suffix)`` for primary option [1]."""
    state = snapshot.state
    package_ok = snapshot.package_status == "DESTINATION_PACKAGE_VERIFIED"
    blocked = bool(getattr(snapshot, "disconnect_blocked", False)) or any(
        token in n.lower()
        for n in snapshot.notes
        for token in ("holder", "handle", "fuser", "lsof", "busy")
    )

    if state == StorageLifecycleState.DEVICE_IDENTITY_MISMATCH:
        return "Diagnose attached storage", ""
    if state in {
        StorageLifecycleState.DETACHED,
        StorageLifecycleState.DEVICE_NOT_FOUND,
    }:
        return "Reconnect or inspect Mercury HDD", ""
    if state == StorageLifecycleState.ATTACHED_READ_ONLY:
        return "Continue destination validation", ""
    if state == StorageLifecycleState.ATTACHED_WRITER_ENABLED:
        return "Prepare HDD for safe disconnect", ""
    if blocked or (
        state == StorageLifecycleState.PREPARING_TO_DISCONNECT and package_ok
    ):
        return "Recheck disconnect blockers", ""
    if state == StorageLifecycleState.READY_TO_DISCONNECT:
        return "Safe disconnect Mercury HDD", "ready"
    if package_ok and state == StorageLifecycleState.ATTACHED_WRITER_DISABLED:
        return "Safe disconnect Mercury HDD", "ready"
    if not package_ok and state in {
        StorageLifecycleState.ATTACHED_WRITER_DISABLED,
        StorageLifecycleState.ATTACHED_UNVALIDATED,
        StorageLifecycleState.PREPARING_TO_DISCONNECT,
    }:
        return "Verify destination package", ""
    if state == StorageLifecycleState.ATTACHED_UNVALIDATED:
        return "Storage status and validation", ""
    if state == StorageLifecycleState.RECONNECT_VALIDATED:
        return "Reconnect or change storage mode", ""
    return "Storage status and validation", ""


def hdd_menu_header_state(snapshot: StorageLifecycleSnapshot) -> str:
    """Operator-facing State field — avoid duplicating 'safe disconnect ready' here."""
    if snapshot.state in {
        StorageLifecycleState.READY_TO_DISCONNECT,
        StorageLifecycleState.PREPARING_TO_DISCONNECT,
        StorageLifecycleState.ATTACHED_WRITER_DISABLED,
    }:
        return "Connected · mounted · writes disabled"
    if snapshot.state == StorageLifecycleState.ATTACHED_WRITER_ENABLED:
        return "Connected · mounted · backups enabled"
    if snapshot.state == StorageLifecycleState.ATTACHED_READ_ONLY:
        return "Connected · mounted read-only"
    if snapshot.state in {
        StorageLifecycleState.DETACHED,
        StorageLifecycleState.DEVICE_NOT_FOUND,
    }:
        return "Not connected"
    return snapshot.label


def host_role_header(snapshot: StorageLifecycleSnapshot) -> str:
    if snapshot.host_role == MigrationHostRole.DESTINATION_REHEARSAL:
        return "Source · destination rehearsal"
    if snapshot.host_role == MigrationHostRole.DESTINATION_VALIDATED:
        return "Destination validated"
    if snapshot.host_role == MigrationHostRole.FINAL_CUTOVER:
        return "Final cutover"
    return "Source operation"


def dashboard_hdd_status_line(snapshot: StorageLifecycleSnapshot) -> str:
    """Main-menu Mercury HDD row (status only; next step is separate)."""
    return hdd_menu_header_state(snapshot)


def dashboard_next_action_short(snapshot: StorageLifecycleSnapshot) -> str:
    """Compact Next action / Recommended row for the main dashboard."""
    from mercury.menu.recommendation import build_main_menu_recommendation

    try:
        return build_main_menu_recommendation(lifecycle=snapshot).explanation
    except Exception:
        # Fallback only if recommendation service cannot load (tests / broken env).
        label, _suffix = recommended_primary_label(snapshot)
        return label


def hdd_menu_render_options(snapshot: StorageLifecycleSnapshot) -> list[tuple[str, str]]:
    """Always four primary actions; [1] is the recommended, state-dependent choice."""
    label, suffix = recommended_primary_label(snapshot)
    # Align ready suffix with mockup spacing.
    first = f"{label}       {suffix}" if suffix else label
    options: list[tuple[str, str]] = [("1", first)]
    for key, fixed_label, _action in PRIMARY_FIXED:
        options.append((key, fixed_label))
    return options


def change_mode_options(snapshot: StorageLifecycleSnapshot) -> list[tuple[str, str, str]]:
    """Contextual reconnect / mode options as ``(key, label, action_id)`` — hide invalid ones."""
    state = snapshot.state
    items: list[tuple[str, str]] = []

    if state in {
        StorageLifecycleState.DETACHED,
        StorageLifecycleState.DEVICE_NOT_FOUND,
    }:
        items = [
            ("Reconnect and inspect read-only", MODE_INSPECT_RO),
            ("Reconnect as source backup writer", MODE_RESTORE_WRITER),
            ("Prepare attached HDD for destination rehearsal", MODE_DESTINATION_REHEARSAL),
        ]
    elif state == StorageLifecycleState.ATTACHED_WRITER_ENABLED:
        items = [
            ("Disable writes and prepare disconnect", MODE_PREPARE_DISCONNECT),
            ("Switch to read-only inspection", MODE_INSPECT_RO),
        ]
    elif state == StorageLifecycleState.ATTACHED_READ_ONLY:
        items = [
            ("Continue read-only inspection", MODE_CONTINUE_RO),
            ("Enable source-host writer", MODE_RESTORE_WRITER),
            ("Keep writes disabled", MODE_KEEP_WRITES_DISABLED),
        ]
    elif state in {
        StorageLifecycleState.READY_TO_DISCONNECT,
        StorageLifecycleState.PREPARING_TO_DISCONNECT,
        StorageLifecycleState.ATTACHED_WRITER_DISABLED,
        StorageLifecycleState.RECONNECT_VALIDATED,
    }:
        # Writes already off / detach prep: never offer enable here as a bare toggle;
        # restore writer is an explicit reconnect path with confirmation.
        items = [
            ("Inspect HDD read-only", MODE_INSPECT_RO),
            ("Restore source-host backup writer", MODE_RESTORE_WRITER),
            ("Keep writes disabled", MODE_KEEP_WRITES_DISABLED),
        ]
    elif state == StorageLifecycleState.DEVICE_IDENTITY_MISMATCH:
        items = [
            ("Inspect HDD read-only", MODE_INSPECT_RO),
        ]
    else:
        items = [
            ("Inspect HDD read-only", MODE_INSPECT_RO),
            ("Restore source-host backup writer", MODE_RESTORE_WRITER),
            ("Keep writes disabled", MODE_KEEP_WRITES_DISABLED),
        ]

    return [(str(i), label, action) for i, (label, action) in enumerate(items, start=1)]


def cleanup_advanced_options(*, cleanup_locked: bool = True) -> list[tuple[str, str, str]]:
    """Cleanup and advanced tools — symbolic IDs; destructive cleanup stays locked via routing."""
    _ = cleanup_locked
    return [
        ("1", "Cleanup status", ADV_CLEANUP_STATUS),
        ("2", "Preview cleanup plan", ADV_CLEANUP_PREVIEW),
        ("3", "Detailed device information", ADV_DEVICE_DETAIL),
        ("4", "SMART health", ADV_SMART),
        ("5", "Archive and legacy USB tools", ADV_ARCHIVE_USB),
        ("6", "Troubleshooting", ADV_TROUBLESHOOT),
    ]


def primary_action_count(snapshot: StorageLifecycleSnapshot) -> int:
    return len(hdd_menu_render_options(snapshot))
