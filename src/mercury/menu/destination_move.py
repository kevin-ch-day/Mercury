"""Destination-move operator status (read-only; no package membership changes)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mercury import output
from mercury.storage.host_maintenance import HostMaintenanceState, load_host_maintenance, writes_allowed
from mercury.storage.retention import RetentionPolicy, load_retention_policy
from mercury.terminal.format import format_package_id_snapshot, short_commit
from mercury.terminal.theme import dashboard_row, rule_line


# Symbolic hub action IDs (numbers assigned after ordering).
HUB_SAFE_DISCONNECT = "safe_disconnect"
HUB_REVIEW_PACKAGE = "review_package"
HUB_RECEIVER_GUIDE = "receiver_guide"
HUB_DESTINATION_STATUS = "destination_status"
HUB_ADVANCED_HANDOFF = "advanced_handoff"


@dataclass(frozen=True)
class DestinationMoveStatus:
    """Operator-facing destination-move snapshot for the current source package."""

    package_status: str
    package_id: str
    package_id_short: str
    mercury_line: str
    erebus_line: str
    databases_line: str
    source_state: str
    hdd_line: str
    destination_state: str
    recommended: str
    snapshot_local: str = ""
    phase3b_backup_ids: tuple[str, ...] = ()
    intake_note: str = "Phase 3B production dumps + Mercury/Erebus captures"
    unresolved_inputs: str = "Destination host not attached"


def destination_progress_label(
    *,
    host: HostMaintenanceState | None = None,
    retention: RetentionPolicy | None = None,
) -> str:
    """Derive destination progress for labels and status rows.

    Source-host disconnect preparation is not destination-host validation.
    """
    state = host or load_host_maintenance()
    policy = retention or load_retention_policy()
    if not policy.destination_validation_pending:
        return "Validation passed"

    source_move_prep = bool(
        state.source_detach_preparation or state.storage_availability == "detaching"
    )
    if source_move_prep:
        return "Not started"

    validating = bool(
        state.destination_rehearsal_active or state.destination_rehearsal_in_progress
    )
    if validating:
        return "Validation active"

    # Destination host registered / planned without validation started.
    if state.destination_rehearsal_planned and state.package_verification_status == (
        "DESTINATION_PACKAGE_VERIFIED"
    ):
        return "Registered · validation not started"

    return "Not started"


def destination_move_action_label(*, host=None, retention=None) -> str:
    """Startup / recommended wording for the destination-move intent."""
    progress = destination_progress_label(host=host, retention=retention)
    if progress in {
        "Validation active",
        "Validation passed",
        "Registered · validation not started",
    }:
        return "Continue destination validation"
    return "Prepare destination move"


def _short_package_id(package_id: str, *, max_len: int = 52) -> str:
    text = (package_id or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _load_package_manifest(package_id: str) -> dict:
    if not package_id:
        return {}
    try:
        from mercury.core.usb_mount import resolve_operator_mount
        from mercury.migration.destination_package_create import packages_root

        mount = resolve_operator_mount()
        path = packages_root(Path(mount)) / package_id / "package_manifest.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _source_state_line(host: HostMaintenanceState) -> str:
    if host.source_data_changed_since_package:
        return "Known source-data changes since package"
    if host.recovery_artifacts_created_after_package or host.source_changed_since_package:
        return "Newer recovery artifacts since package"
    if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED":
        return "No known changes since package"
    return "Unknown"


def _hdd_line(host: HostMaintenanceState) -> str:
    if host.storage_availability == "detached":
        return "Not connected"
    if writes_allowed(host):
        return "Mounted · writes enabled"
    return "Mounted · writes disabled"


def _recommended_for_status(host: HostMaintenanceState) -> str:
    if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED" and not writes_allowed(
        host
    ):
        return "Safely disconnect Mercury HDD"
    if host.package_verification_status != "DESTINATION_PACKAGE_VERIFIED":
        return "Verify destination package"
    return destination_move_action_label(host=host)


def build_destination_move_status(
    *,
    host: HostMaintenanceState | None = None,
    retention: RetentionPolicy | None = None,
    manifest: dict | None = None,
) -> DestinationMoveStatus:
    """Build a read-only destination-move status snapshot."""
    state = host or load_host_maintenance()
    policy = retention or load_retention_policy()
    package_id = (state.package_id or "").strip()
    payload = manifest if manifest is not None else _load_package_manifest(package_id)

    verified = state.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
    if verified:
        package_status = "VERIFIED"
    elif state.package_verification_status:
        package_status = "Not verified"
    else:
        package_status = "Missing"

    mercury_commit = str(payload.get("mercury_commit") or policy.current_destination_mercury_commit or "")
    mercury_capture = str(
        payload.get("mercury_capture_id") or policy.current_destination_mercury_capture_id or ""
    )
    erebus_commit = str(payload.get("erebus_commit") or policy.current_erebus_destination_commit or "")
    erebus_capture = str(
        payload.get("erebus_capture_id")
        or (policy.protected_capture_ids[0] if policy.protected_capture_ids else "")
    )
    backup_ids = tuple(
        str(item)
        for item in (payload.get("included_backup_ids") or list(policy.protected_backup_ids))
        if str(item).strip()
    )

    if mercury_commit or mercury_capture:
        mercury_line = (
            f"Captured · {short_commit(mercury_commit)}"
            + (f" / {mercury_capture}" if mercury_capture else "")
        )
    else:
        mercury_line = "Capture not recorded on this host view"

    if erebus_commit or erebus_capture:
        erebus_line = (
            f"Captured · {short_commit(erebus_commit)}"
            + (f" / {erebus_capture}" if erebus_capture else "")
        )
    else:
        erebus_line = "Capture not recorded on this host view"

    databases_line = (
        "Phase 3B dumps verified" if backup_ids and verified else "Phase 3B dumps pending review"
    )

    snapshot_local = ""
    if package_id:
        from mercury.terminal.format import format_package_id_snapshot

        snapshot_local = format_package_id_snapshot(package_id) or ""

    progress = destination_progress_label(host=state, retention=policy)
    unresolved = (
        "None · destination validation complete"
        if progress == "Validation passed"
        else "Destination host not attached"
        if progress == "Not started"
        else "Destination validation inputs unresolved"
    )

    return DestinationMoveStatus(
        package_status=package_status,
        package_id=package_id or "(none)",
        package_id_short=_short_package_id(package_id or "(none)"),
        mercury_line=mercury_line,
        erebus_line=erebus_line,
        databases_line=databases_line,
        source_state=_source_state_line(state),
        hdd_line=_hdd_line(state),
        destination_state=progress,
        recommended=_recommended_for_status(state),
        snapshot_local=snapshot_local,
        phase3b_backup_ids=backup_ids,
        unresolved_inputs=unresolved,
    )


def render_destination_move_status_lines(status: DestinationMoveStatus) -> list[str]:
    """Colon-free status rows for DESTINATION MOVE screens."""
    lines = [
        dashboard_row("Package", status.package_status, label_width=14),
        dashboard_row("Package ID", status.package_id_short, label_width=14),
        dashboard_row("Mercury", status.mercury_line, label_width=14),
        dashboard_row("Erebus", status.erebus_line, label_width=14),
        dashboard_row("Databases", status.databases_line, label_width=14),
        dashboard_row("Source state", status.source_state, label_width=14),
        dashboard_row("Mercury HDD", status.hdd_line, label_width=14),
        dashboard_row("Destination", status.destination_state, label_width=14),
        dashboard_row("Recommended", status.recommended, label_width=14),
    ]
    if status.snapshot_local:
        lines.insert(2, dashboard_row("Snapshot", status.snapshot_local, label_width=14))
    return lines


def print_destination_move_status(
    status: DestinationMoveStatus | None = None, *, with_title: bool = True
) -> None:
    snap = status or build_destination_move_status()
    if with_title:
        from mercury.terminal.theme import colors_enabled, section_title

        if colors_enabled():
            output.write(section_title("DESTINATION MOVE"))
        else:
            output.write("DESTINATION MOVE")
        output.write(rule_line(level="normal"))
    for line in render_destination_move_status_lines(snap):
        output.write(line)


def build_destination_hub_options(
    *, host: HostMaintenanceState | None = None
) -> list[tuple[str, str, str]]:
    """Ordered ``(key, label, action_id)`` for the destination-move hub."""
    state = host or load_host_maintenance()
    verified = state.package_verification_status == "DESTINATION_PACKAGE_VERIFIED"
    writes_off = not writes_allowed(state)
    recommend_disconnect = verified and writes_off and not (
        destination_progress_label(host=state) in {"Validation active", "Validation passed"}
    )

    ordered: list[tuple[str, str]] = []
    if recommend_disconnect:
        ordered.append((HUB_SAFE_DISCONNECT, "Safely disconnect Mercury HDD"))
    ordered.extend(
        [
            (HUB_REVIEW_PACKAGE, "Review destination package"),
            (HUB_RECEIVER_GUIDE, "Open receiver guide"),
            (HUB_DESTINATION_STATUS, "Destination checklist and status"),
        ]
    )
    if not recommend_disconnect:
        ordered.append((HUB_SAFE_DISCONNECT, "Safely disconnect Mercury HDD"))
    ordered.append((HUB_ADVANCED_HANDOFF, "Advanced handoff tools"))

    recommended = HUB_SAFE_DISCONNECT if recommend_disconnect else HUB_DESTINATION_STATUS
    options: list[tuple[str, str, str]] = []
    for index, (action_id, label) in enumerate(ordered, start=1):
        suffix = "       recommended" if action_id == recommended else ""
        options.append((str(index), f"{label}{suffix}", action_id))
    return options


def receiver_guide_lines_for_package(package_id: str) -> list[str]:
    """Receiver sequence pinned to the exact verified package ID."""
    pkg = package_id.strip() or "(no package id)"
    return [
        f"Receiver guide for:",
        f"  {pkg}",
        "",
        "Expected destination sequence:",
        "  1. Attach Mercury HDD",
        "  2. Inspect read-only",
        "  3. Verify package checksums",
        "  4. Reconstruct Mercury",
        "  5. Reconstruct Erebus",
        "  6. Provision configuration and secrets",
        "  7. Restore Phase 3B into disposable schemas",
        "  8. Run comparator and doctor checks",
    ]


def print_package_receiver_guide(*, package_id: str | None = None) -> None:
    host = load_host_maintenance()
    pkg = package_id if package_id is not None else host.package_id
    output.write("RECEIVER GUIDE")
    output.write(rule_line())
    for line in receiver_guide_lines_for_package(pkg):
        output.write(line)
