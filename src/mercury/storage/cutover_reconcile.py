"""Governed repair for stale host-maintenance after a completed HDD cutover."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.storage_roles import MigrationState, StorageWriteRole
from mercury.core.storage_roots import load_storage_config
from mercury.migration.generation import read_cutover_receipt, read_verified_generation, write_immutable_receipt
from mercury.storage.cutover_approve import _post_cutover_host_state, _validate_post_cutover_host_state
from mercury.storage.host_maintenance import load_host_maintenance, save_host_maintenance, writes_allowed
from mercury.storage.report import build_storage_status_report
from mercury.storage.transitions import append_transition_ledger, detect_active_operations, new_transition_id

CONFIRMATION = "RECONCILE HDD CUTOVER"


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable package evidence: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _verify_package(*, package_root: Path, package_id: str) -> dict:
    root = package_root.expanduser().resolve()
    if not package_id or package_id.lower() == "latest" or root.name != package_id:
        raise ValueError("An exact package ID matching package-root basename is required.")
    receipt = _read_object(root / "package_receipt.json")
    if receipt.get("package_id") != package_id or receipt.get("verification_status") != "DESTINATION_PACKAGE_VERIFIED":
        raise ValueError("Package is not the exact DESTINATION_PACKAGE_VERIFIED authority.")
    from mercury.storage.detach_wizard import verify_package_manifest

    errors = verify_package_manifest(root)
    if errors:
        raise ValueError("Package checksum verification failed: " + "; ".join(errors))
    return receipt


def reconcile_completed_hdd_cutover(*, confirmation: str, package_root: Path, package_id: str) -> Path:
    """Reconcile only stale host state for an already-complete writer cutover.

    This never changes local.toml, mounts, databases, or package contents.
    """
    if confirmation.strip() != CONFIRMATION:
        raise ValueError(f"Type {CONFIRMATION} to reconcile the completed HDD cutover.")
    config = load_storage_config(warn_deprecated=False)
    if config.active_write_role != StorageWriteRole.PRIMARY or config.migration_state != MigrationState.CUTOVER_COMPLETE:
        raise ValueError("Storage configuration is not an already-complete primary HDD cutover.")
    status = build_storage_status_report()
    if not status.primary.validation.ok:
        raise ValueError("Primary HDD mount/identity validation failed; host state was not changed.")
    cutover = read_cutover_receipt(config=config)
    if not cutover:
        raise ValueError("Existing governed cutover receipt is missing.")
    if cutover.get("hdd_uuid") != config.primary.filesystem_uuid or cutover.get("usb_uuid") != config.legacy.filesystem_uuid:
        raise ValueError("Existing cutover receipt UUID identities do not match configured storage.")
    generation = read_verified_generation(config=config)
    if not generation or cutover.get("cutover_verified_hdd_generation") != generation:
        raise ValueError("Existing cutover generation evidence is missing or inconsistent.")
    package_receipt = _verify_package(package_root=package_root, package_id=package_id)
    active = detect_active_operations()
    if active:
        raise ValueError("Active operation blocks host-state reconciliation: " + ", ".join(active))
    before = load_host_maintenance()
    if (
        writes_allowed(before)
        and before.active_write_role == StorageWriteRole.PRIMARY.value
        and not before.destination_rehearsal_in_progress
    ):
        raise ValueError("Host maintenance already authorizes the primary writer; reconciliation is not needed.")
    after = replace(
        _post_cutover_host_state(before),
        package_id=package_id,
        package_verification_status="DESTINATION_PACKAGE_VERIFIED",
        notes=(
            "Governed host-maintenance reconciliation completed after a verified HDD cutover; "
            "the primary HDD is the only Mercury writer. Legacy USB is offline recovery archive."
        ),
    )
    transition_id = new_transition_id()
    receipt_name = f"host_maintenance_reconciliation_{transition_id}.json"
    receipt = {
        "decision": "HOST_MAINTENANCE_RECONCILED_AFTER_COMPLETED_HDD_CUTOVER",
        "reconciled_at_utc": datetime.now(timezone.utc).isoformat(),
        "transition_id": transition_id,
        "package_id": package_id,
        "package_receipt": str(package_root.expanduser().resolve() / "package_receipt.json"),
        "package_finished_at_utc": package_receipt.get("finished_at_utc"),
        "cutover_receipt": str(config.primary.control_dir / "cutover_receipt.json"),
        "cutover_generation": generation,
        "hdd_uuid": config.primary.filesystem_uuid,
        "usb_uuid": config.legacy.filesystem_uuid,
        "host_maintenance_before": asdict(before),
        "host_maintenance_after": asdict(after),
        "local_config_changed": False,
        "mounts_changed": False,
        "databases_changed": False,
    }
    try:
        save_host_maintenance(after)
        _validate_post_cutover_host_state(load_host_maintenance())
        receipt_path = write_immutable_receipt(receipt_name, receipt, config=config)
        append_transition_ledger(
            {
                "transition": "completed_hdd_cutover_host_maintenance_reconciliation",
                "transition_id": transition_id,
                "result": "SUCCESS",
                "package_id": package_id,
                "receipt": str(receipt_path),
                "hdd_uuid": config.primary.filesystem_uuid,
                "usb_uuid": config.legacy.filesystem_uuid,
            }
        )
    except Exception as exc:
        save_host_maintenance(before)
        raise ValueError("Host-maintenance reconciliation failed; prior host state was restored.") from exc
    return receipt_path
