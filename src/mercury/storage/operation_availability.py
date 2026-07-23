"""Classify whether an operator action may proceed, recover, or hard-block."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from mercury.core.storage_roles import DEFAULT_PRIMARY_MOUNT, DEFAULT_PRIMARY_UUID
from mercury.menu.options import MAIN_STORAGE, main_menu_hint
from mercury.storage.hdd_menu_options import STORAGE_CHANGE_MODE, hdd_menu_hint
from mercury.storage.host_maintenance import HostMaintenanceState, load_host_maintenance
from mercury.storage.transitions import (
    RESTORE_SOURCE_WRITER_PHRASE,
    StorageFacts,
    TransitionName,
    TransitionStatus,
    observe_storage_facts,
    record_backup_continuation,
    restore_source_writer,
)


class AvailabilityClassification(str, Enum):
    """Whether an operation may proceed given current storage facts."""

    AVAILABLE = "AVAILABLE"
    RECOVERABLE_CONFIRMATION = "RECOVERABLE_CONFIRMATION"
    STRONG_CONFIRMATION = "STRONG_CONFIRMATION"
    HARD_BLOCK = "HARD_BLOCK"


class OperationStatus(str, Enum):
    """Outcome of the operator-facing action (separate from storage transition)."""

    NOT_STARTED = "NOT_STARTED"
    READY = "READY"
    CONTINUED = "CONTINUED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ConfirmationType(str, Enum):
    NONE = "none"
    YES_NO = "yes_no"
    EXACT_PHRASE = "exact_phrase"


@dataclass(frozen=True)
class OperationAvailability:
    operation: str
    classification: AvailabilityClassification
    available: bool
    recovery_transition: str = ""
    confirmation_type: ConfirmationType = ConfirmationType.NONE
    confirmation_phrase: str = ""
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    next_action: str = ""
    facts: StorageFacts | None = None
    detail_lines: tuple[str, ...] = ()
    operation_status: OperationStatus = OperationStatus.NOT_STARTED
    transition_status: TransitionStatus | None = None
    transition_id: str = ""

    def __bool__(self) -> bool:
        return self.available

    @property
    def is_recoverable(self) -> bool:
        return self.classification == AvailabilityClassification.RECOVERABLE_CONFIRMATION

    @property
    def is_strong(self) -> bool:
        return self.classification == AvailabilityClassification.STRONG_CONFIRMATION

    @property
    def is_hard_block(self) -> bool:
        return self.classification == AvailabilityClassification.HARD_BLOCK


def _storage_next_action() -> str:
    return f"{main_menu_hint(MAIN_STORAGE)} → {hdd_menu_hint(STORAGE_CHANGE_MODE)}"


def _destination_rehearsal_active(state: HostMaintenanceState, facts: StorageFacts) -> bool:
    return bool(
        state.destination_rehearsal_active
        or state.destination_rehearsal_in_progress
        or facts.destination_rehearsal_active
        or facts.destination_rehearsal_in_progress
    )


def assess_operation_availability(
    operation: str = "database_backup",
    *,
    host: HostMaintenanceState | None = None,
    facts: StorageFacts | None = None,
    resolve_fn: Callable[..., Any] | None = None,
) -> OperationAvailability:
    """Classify availability for HDD-backed operator actions (Phase 1: backup)."""
    state = host or load_host_maintenance()
    observed = facts or observe_storage_facts(host=state, resolve_fn=resolve_fn)

    if (
        observed.writes_allowed
        and state.storage_availability not in {"detaching", "detached"}
        and not state.source_detach_preparation
    ):
        return OperationAvailability(
            operation=operation,
            classification=AvailabilityClassification.AVAILABLE,
            available=True,
            facts=observed,
            detail_lines=(
                f"Mercury HDD: {observed.compact_line}",
                f"Active writer: {observed.active_write_role}",
            ),
            operation_status=OperationStatus.READY,
        )

    blockers: list[str] = []
    if not observed.device_connected:
        blockers.append("Mercury cannot locate the approved HDD")
    if observed.identity_mismatch or (
        observed.device_connected and not observed.filesystem_uuid_valid
    ):
        blockers.append("Device UUID does not match the approved Mercury HDD")
    if observed.desktop_automount or (
        observed.mountpoint.startswith("/run/media") if observed.mountpoint else False
    ):
        blockers.append("HDD is mounted under /run/media instead of the contract mount")
    if observed.filesystem_mounted and not observed.expected_mountpoint:
        blockers.append(f"Expected mountpoint {DEFAULT_PRIMARY_MOUNT}")
    if observed.active_operation:
        for name in observed.active_operation.split(","):
            token = name.strip().lower()
            if not token:
                continue
            if token in {
                "package_create",
                "package_transfer",
                "package_consume",
                "package",
            }:
                blockers.append(f"Active package operation in progress: {name.strip()}")
            else:
                blockers.append(f"Active operation in progress: {name.strip()}")
    if state.storage_availability == "detached" and not observed.device_connected:
        blockers.append("Mercury HDD is detached")

    if blockers:
        expected = f"Expected:\n  MERCURY_DATA_V2\n  UUID {DEFAULT_PRIMARY_UUID}"
        return OperationAvailability(
            operation=operation,
            classification=AvailabilityClassification.HARD_BLOCK,
            available=False,
            blockers=tuple(blockers),
            next_action=_storage_next_action(),
            facts=observed,
            detail_lines=(expected,),
            operation_status=OperationStatus.BLOCKED,
        )

    if observed.mount_mode == "read-only":
        return OperationAvailability(
            operation=operation,
            classification=AvailabilityClassification.HARD_BLOCK,
            available=False,
            blockers=(
                "Filesystem is mounted read-only and cannot safely receive backup writes",
            ),
            next_action=_storage_next_action(),
            facts=observed,
            detail_lines=(
                f"Mercury HDD: {observed.compact_line}",
                "Remount read-write via Reconnect or change storage mode before backing up.",
            ),
            operation_status=OperationStatus.BLOCKED,
        )

    strong_reasons: list[str] = []
    if _destination_rehearsal_active(state, observed):
        strong_reasons.append(
            "A verified destination rehearsal package exists and destination rehearsal "
            "is marked active."
            if state.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
            else "Destination rehearsal is marked active."
        )
    if "migration lock" in (state.notes or "").lower():
        strong_reasons.append("Migration lock is active")

    if strong_reasons and observed.device_connected and observed.filesystem_mounted:
        return OperationAvailability(
            operation=operation,
            classification=AvailabilityClassification.STRONG_CONFIRMATION,
            available=False,
            recovery_transition=TransitionName.RESTORE_SOURCE_WRITER.value,
            confirmation_type=ConfirmationType.EXACT_PHRASE,
            confirmation_phrase=RESTORE_SOURCE_WRITER_PHRASE,
            warnings=tuple(strong_reasons)
            + (
                "Restoring source writes allows Mercury to create new backups, Git captures, "
                "and other recovery artifacts that are not included in the current package.",
                "The existing package remains valid for destination rehearsal, but it will "
                "not include anything created by this new session.",
            ),
            next_action=_storage_next_action(),
            facts=observed,
            detail_lines=tuple(strong_reasons),
            operation_status=OperationStatus.NOT_STARTED,
        )

    # Recoverable: unfinished disconnect prep only — no destination rehearsal / migration lock.
    detach_prep = (
        state.source_detach_preparation
        or state.storage_availability == "detaching"
        or "detach" in (state.notes or "").lower()
        or "disconnect" in (state.notes or "").lower()
    )
    recoverable = (
        detach_prep
        and observed.device_connected
        and observed.filesystem_mounted
        and observed.filesystem_uuid_valid
        and observed.filesystem_type_valid
        and observed.expected_mountpoint
        and observed.mount_mode in {"read-write", "unknown"}
        and not observed.writes_allowed
        and not _destination_rehearsal_active(state, observed)
        and "migration lock" not in (state.notes or "").lower()
        and not observed.active_operation
    )
    if recoverable:
        return OperationAvailability(
            operation=operation,
            classification=AvailabilityClassification.RECOVERABLE_CONFIRMATION,
            available=False,
            recovery_transition=TransitionName.CANCEL_DISCONNECT_PREPARATION.value,
            confirmation_type=ConfirmationType.YES_NO,
            facts=observed,
            detail_lines=(
                "The Mercury HDD is connected, mounted, and healthy.",
                "Backup writes are disabled because a previous session prepared the HDD "
                "for safe disconnect.",
                "",
                "To continue, Mercury must:",
                "  1. Cancel disconnect preparation",
                "  2. Revalidate the Mercury HDD",
                "  3. Restore the source backup writer",
                "  4. Enable governed HDD writes",
            ),
            next_action=_storage_next_action(),
            operation_status=OperationStatus.NOT_STARTED,
        )

    return OperationAvailability(
        operation=operation,
        classification=AvailabilityClassification.HARD_BLOCK,
        available=False,
        blockers=("Mercury writes are disabled and the state is not safely recoverable",),
        next_action=_storage_next_action(),
        facts=observed,
        detail_lines=(
            f"Mercury HDD: {observed.compact_line}",
            f"Storage availability: {observed.storage_availability}",
        ),
        operation_status=OperationStatus.BLOCKED,
    )


def format_hard_block_message(availability: OperationAvailability) -> str:
    lines = ["BACKUP BLOCKED", "─" * 62]
    for blocker in availability.blockers:
        lines.append(blocker)
    for detail in availability.detail_lines:
        lines.append(detail)
    if availability.next_action:
        lines.extend(["", "Next:", f"  {availability.next_action}"])
    return "\n".join(lines)


def format_recoverable_prompt(availability: OperationAvailability) -> str:
    lines = ["BACKUP WRITER DISABLED", "─" * 62]
    lines.extend(availability.detail_lines)
    return "\n".join(lines)


def format_strong_prompt(availability: OperationAvailability) -> str:
    lines = [
        "SOURCE WRITER RESTORE REQUIRES CONFIRMATION",
        "─" * 62,
    ]
    for detail in availability.detail_lines:
        lines.append(detail)
    lines.append("")
    for warning in availability.warnings:
        if warning in availability.detail_lines:
            continue
        lines.append(warning)
    lines.extend(
        [
            "",
            "Type exactly:",
            "",
            f"  {availability.confirmation_phrase or RESTORE_SOURCE_WRITER_PHRASE}",
            "",
            "or press Enter to cancel:",
        ]
    )
    return "\n".join(lines)


def ensure_backup_writes_available(
    *,
    interactive: bool = True,
    ask_yes_no: Callable[[str, bool], bool | None] | None = None,
    ask_phrase: Callable[[str], str] | None = None,
    write: Callable[[str], None] | None = None,
    host: HostMaintenanceState | None = None,
    resolve_fn: Callable[..., Any] | None = None,
    path=None,
) -> OperationAvailability:
    """Classify and optionally recover so a backup may continue.

    Returns availability for the backup operation. Transition outcome is exposed via
    ``transition_status`` / ``transition_id`` and is separate from backup success.
    """
    from mercury import output
    from mercury.menu import prompts as menu_prompts

    write_fn = write or output.write
    availability = assess_operation_availability(
        "database_backup", host=host, resolve_fn=resolve_fn
    )
    if availability.available:
        return availability

    if availability.is_hard_block:
        write_fn(format_hard_block_message(availability))
        return availability

    # Non-interactive callers may inject ask_yes_no / ask_phrase (CLI --accept-restore /
    # --confirm-restore). Without those callbacks, refuse rather than prompting.
    if not interactive and ask_yes_no is None and ask_phrase is None:
        write_fn(format_hard_block_message(availability))
        return availability

    if availability.is_recoverable:
        write_fn(format_recoverable_prompt(availability))
        write_fn("")
        if ask_yes_no is not None:
            accepted = ask_yes_no("Restore the backup writer and continue?", False)
        elif not interactive:
            write_fn(format_hard_block_message(availability))
            return availability
        else:
            accepted = menu_prompts.ask_yes_no(
                "Restore the backup writer and continue?", default=False
            )
        if accepted is not True:
            write_fn("Backup cancelled. Mercury writes remain disabled.")
            return OperationAvailability(
                operation=availability.operation,
                classification=availability.classification,
                available=False,
                recovery_transition=availability.recovery_transition,
                confirmation_type=availability.confirmation_type,
                blockers=("operator declined writer restoration",),
                next_action=availability.next_action,
                facts=availability.facts,
                detail_lines=availability.detail_lines,
                operation_status=OperationStatus.CANCELLED,
                transition_status=TransitionStatus.CANCELLED,
            )
        transition = restore_source_writer(
            operator_intent="backup_restore_and_continue",
            path=path,
            require_strong_phrase=False,
            resolve_fn=resolve_fn,
            confirmation_class="RECOVERABLE_CONFIRMATION",
        )
        if not transition.ok:
            write_fn("Writer restoration failed. Backup was not started.")
            for blocker in transition.blockers:
                write_fn(f"  {blocker}")
            return OperationAvailability(
                operation=availability.operation,
                classification=availability.classification,
                available=False,
                blockers=tuple(transition.blockers),
                next_action=availability.next_action,
                facts=availability.facts,
                operation_status=OperationStatus.FAILED,
                transition_status=transition.status,
                transition_id=transition.transition_id,
            )
        write_fn("Source backup writer restored. Continuing backup…")
        refreshed = assess_operation_availability(
            "database_backup", resolve_fn=resolve_fn
        )
        return OperationAvailability(
            operation=refreshed.operation,
            classification=refreshed.classification,
            available=refreshed.available,
            facts=refreshed.facts,
            detail_lines=refreshed.detail_lines,
            operation_status=OperationStatus.CONTINUED,
            transition_status=transition.status,
            transition_id=transition.transition_id,
        )

    if availability.is_strong:
        write_fn(format_strong_prompt(availability))
        if ask_phrase is not None:
            phrase = ask_phrase("").strip()
        elif not interactive:
            write_fn(format_hard_block_message(availability))
            return availability
        else:
            phrase = menu_prompts.ask("").strip()
        if phrase != availability.confirmation_phrase:
            write_fn("Backup cancelled. Mercury writes remain disabled.")
            return OperationAvailability(
                operation=availability.operation,
                classification=availability.classification,
                available=False,
                blockers=("confirmation phrase mismatch",),
                next_action=availability.next_action,
                facts=availability.facts,
                operation_status=OperationStatus.CANCELLED,
                transition_status=TransitionStatus.CANCELLED,
            )
        transition = restore_source_writer(
            confirm=phrase,
            operator_intent="backup_strong_restore_and_continue",
            path=path,
            require_strong_phrase=True,
            resolve_fn=resolve_fn,
            confirmation_class="STRONG_CONFIRMATION",
        )
        if not transition.ok:
            write_fn("Writer restoration failed. Backup was not started.")
            for blocker in transition.blockers:
                write_fn(f"  {blocker}")
            return OperationAvailability(
                operation=availability.operation,
                classification=availability.classification,
                available=False,
                blockers=tuple(transition.blockers),
                next_action=availability.next_action,
                facts=availability.facts,
                operation_status=OperationStatus.FAILED,
                transition_status=transition.status,
                transition_id=transition.transition_id,
            )
        write_fn("Source backup writer restored. Continuing backup…")
        refreshed = assess_operation_availability(
            "database_backup", resolve_fn=resolve_fn
        )
        return OperationAvailability(
            operation=refreshed.operation,
            classification=refreshed.classification,
            available=refreshed.available,
            facts=refreshed.facts,
            detail_lines=refreshed.detail_lines,
            operation_status=OperationStatus.CONTINUED,
            transition_status=transition.status,
            transition_id=transition.transition_id,
        )

    write_fn(format_hard_block_message(availability))
    return availability


def note_backup_after_transition(
    availability: OperationAvailability,
    *,
    backup_ran: bool,
    backup_succeeded: bool | None = None,
) -> None:
    """Record whether the continued backup actually executed after a transition."""
    if not availability.transition_id:
        return
    try:
        record_backup_continuation(
            transition_id=availability.transition_id,
            backup_ran=backup_ran,
            backup_succeeded=backup_succeeded,
        )
    except OSError:
        pass
