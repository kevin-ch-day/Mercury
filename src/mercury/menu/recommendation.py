"""Main-menu recommendation service (Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mercury.menu.options import (
    MAIN_ADVANCED,
    MAIN_BACKUP_SYNC,
    MAIN_HEALTH,
    MAIN_MIGRATION,
    MAIN_RECOVERY,
    MAIN_REPORTS,
    MAIN_STORAGE,
)


@dataclass(frozen=True)
class MainMenuRecommendation:
    host_role: str
    storage_state: str
    migration_state: str
    backup_state: str
    package_state: str
    recommended_action: str
    explanation: str
    allowed_actions: tuple[str, ...] = field(default_factory=tuple)
    software_only: bool = False
    intent_chooser_required: bool = False
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def recommended_label(self) -> str:
        labels = {
            MAIN_BACKUP_SYNC: "Back up and sync this workstation",
            MAIN_STORAGE: "Mercury HDD and Storage",
            MAIN_RECOVERY: "Restore and disaster recovery",
            MAIN_REPORTS: "Reports and backup history",
            MAIN_MIGRATION: "Workstation migration",
            MAIN_HEALTH: "System health and configuration",
            MAIN_ADVANCED: "Advanced tools",
            "intent_chooser": "Choose backup, disconnect, or rehearsal",
            "reconnect": "Reconnect and validate Mercury HDD",
            "attach": "Attach HDD and choose Reconnect",
            "diagnose": "Diagnose attached storage",
            "destination_validation": "Continue destination validation",
        }
        return labels.get(self.recommended_action, self.explanation)


def build_main_menu_recommendation(
    *,
    host=None,
    lifecycle=None,
    resolve_fn=None,
) -> MainMenuRecommendation:
    """Derive the recommended main-console action from storage/migration facts."""
    from mercury.storage.host_maintenance import load_host_maintenance
    from mercury.storage.lifecycle import assess_storage_lifecycle

    state = host or load_host_maintenance()
    try:
        snap = lifecycle or assess_storage_lifecycle(
            host=state, probe_disconnect=False, resolve_fn=resolve_fn
        )
    except Exception:
        snap = None

    # Prefer explicit lifecycle snapshot facts when provided (dashboard/tests).
    if lifecycle is not None and snap is not None and host is None:
        writes = bool(getattr(snap, "writes_allowed", False))
        storage_state = getattr(getattr(snap, "state", None), "value", "unknown")
        availability = (
            "detached"
            if storage_state == "DETACHED"
            else ("detaching" if not writes else "mounted")
        )
        package = (
            "DESTINATION_PACKAGE_VERIFIED"
            if (
                getattr(snap, "package_verified", False)
                or getattr(snap, "package_status", "") == "DESTINATION_PACKAGE_VERIFIED"
            )
            else "Pending"
        )
        package_id = str(getattr(snap, "package_id", "") or "")
        rehearsal = bool(getattr(snap, "destination_rehearsal", False)) or (
            package == "DESTINATION_PACKAGE_VERIFIED" and not writes
        )
        # Lifecycle snapshots used by the dashboard may omit mount flags; treat
        # connected+mounted as true for non-detached states unless explicit.
        if storage_state == "DETACHED":
            connected = False
            mounted = False
        else:
            connected = True
            mounted = bool(getattr(snap, "mounted", True)) or storage_state not in {
                "DEVICE_NOT_FOUND",
                "CONNECTED_UNMOUNTED",
            }
            if hasattr(snap, "mounted") and snap.mount == "" and storage_state in {
                "CONNECTED_UNMOUNTED",
                "DEVICE_NOT_FOUND",
            }:
                mounted = False
        identity_ok = storage_state != "DEVICE_IDENTITY_MISMATCH" and bool(
            getattr(snap, "identity_ok", True)
        )
    else:
        writes = bool(getattr(state, "writes_allowed", False)) and getattr(
            state, "storage_availability", "unknown"
        ) not in {
            "detaching",
            "detached",
        } and not bool(getattr(state, "source_detach_preparation", False))
        availability = str(getattr(state, "storage_availability", None) or "unknown")
        package = str(getattr(state, "package_verification_status", None) or "Pending")
        package_id = str(getattr(state, "package_id", None) or "")
        rehearsal = bool(
            getattr(state, "destination_rehearsal_active", False)
            or getattr(state, "destination_rehearsal_in_progress", False)
        )
        storage_state = availability
        if snap is not None:
            storage_state = getattr(getattr(snap, "state", None), "value", availability)

    host_role = "SOURCE_OPERATION"
    if snap is not None and getattr(snap, "host_role", None) is not None:
        host_role = getattr(snap.host_role, "value", str(snap.host_role))
    elif rehearsal and not writes:
        host_role = "DESTINATION_REHEARSAL"

    migration_state = "destination_validation_pending"
    if package == "DESTINATION_PACKAGE_VERIFIED":
        migration_state = "package_verified_destination_pending"
    backup_state = "unknown"

    if lifecycle is None or host is not None:
        identity_ok = True
        mounted = availability in {"mounted", "attached", "detaching"}
        connected = availability != "detached"
        if snap is not None:
            connected = bool(getattr(snap, "drive_present", connected))
            mounted = bool(getattr(snap, "mounted", mounted))
            identity_ok = bool(getattr(snap, "identity_ok", True))

    allowed = (
        MAIN_BACKUP_SYNC,
        MAIN_STORAGE,
        MAIN_RECOVERY,
        MAIN_REPORTS,
        MAIN_MIGRATION,
        MAIN_HEALTH,
        MAIN_ADVANCED,
    )

    # HDD absent → software-only posture.
    if not connected or availability == "detached":
        return MainMenuRecommendation(
            host_role=host_role,
            storage_state="detached",
            migration_state=migration_state,
            backup_state=backup_state,
            package_state=package,
            recommended_action="attach",
            explanation="Attach HDD and choose Reconnect",
            allowed_actions=(
                MAIN_STORAGE,
                MAIN_RECOVERY,
                MAIN_REPORTS,
                MAIN_HEALTH,
                MAIN_ADVANCED,
            ),
            software_only=True,
            facts={"package_id": package_id},
        )

    if not identity_ok:
        return MainMenuRecommendation(
            host_role=host_role,
            storage_state=storage_state,
            migration_state=migration_state,
            backup_state=backup_state,
            package_state=package,
            recommended_action="diagnose",
            explanation="Diagnose attached storage",
            allowed_actions=allowed,
            facts={"package_id": package_id},
        )

    if connected and not mounted:
        return MainMenuRecommendation(
            host_role=host_role,
            storage_state="connected_unmounted",
            migration_state=migration_state,
            backup_state=backup_state,
            package_state=package,
            recommended_action="reconnect",
            explanation="Reconnect and validate Mercury HDD",
            allowed_actions=allowed,
            facts={"package_id": package_id},
        )

    # Destination read-only / rehearsal focus.
    if host_role == "DESTINATION_REHEARSAL" or (
        rehearsal and not writes and package == "DESTINATION_PACKAGE_VERIFIED"
    ):
        # Writes disabled after detach prep / disconnect readiness → intent chooser.
        if (
            getattr(state, "source_detach_preparation", False)
            or availability == "detaching"
            or storage_state
            in {
                "READY_TO_DISCONNECT",
                "PREPARING_TO_DISCONNECT",
                "ATTACHED_WRITER_DISABLED",
            }
        ):
            return MainMenuRecommendation(
                host_role=host_role,
                storage_state=storage_state,
                migration_state=migration_state,
                backup_state=backup_state,
                package_state=package,
                recommended_action="intent_chooser",
                explanation="Choose backup, disconnect, or rehearsal",
                allowed_actions=allowed,
                intent_chooser_required=True,
                facts={"package_id": package_id, "rehearsal": True},
            )
        return MainMenuRecommendation(
            host_role=host_role,
            storage_state=storage_state,
            migration_state=migration_state,
            backup_state=backup_state,
            package_state=package,
            recommended_action="destination_validation",
            explanation="Continue destination validation",
            allowed_actions=allowed,
            facts={"package_id": package_id},
        )

    if writes:
        return MainMenuRecommendation(
            host_role=host_role,
            storage_state=storage_state,
            migration_state=migration_state,
            backup_state=backup_state,
            package_state=package,
            recommended_action=MAIN_BACKUP_SYNC,
            explanation="Back up and sync this workstation",
            allowed_actions=allowed,
            facts={"package_id": package_id, "writes_allowed": True},
        )

    # Connected, mounted, writes disabled (detach prep unfinished).
    return MainMenuRecommendation(
        host_role=host_role,
        storage_state=storage_state,
        migration_state=migration_state,
        backup_state=backup_state,
        package_state=package,
        recommended_action="intent_chooser",
        explanation="Choose backup, disconnect, or rehearsal",
        allowed_actions=allowed,
        intent_chooser_required=True,
        facts={"package_id": package_id, "writes_allowed": False},
    )
