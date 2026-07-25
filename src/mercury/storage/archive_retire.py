"""Governed retirement of the post-cutover legacy USB as an offline archive."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.migration.generation import (
    read_archive_receipt,
    read_cutover_receipt,
    read_verified_generation,
    write_immutable_receipt,
)
from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed
from mercury.storage.report import build_storage_status_report
from mercury.storage.transitions import append_transition_ledger, detect_active_operations, new_transition_id


CONFIRMATION = "RETIRE LEGACY USB"
RETIREMENT_RECEIPT_FILE = "legacy_usb_retirement.json"


def _safe_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("USB archive receipt contains an invalid relative path.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"USB archive receipt contains an unsafe path: {value!r}")
    return path


def _verify_historical_archive_on_primary(*, config: StorageConfig, archive: dict[str, Any]) -> tuple[int, int, int]:
    """Ensure every durable entry recorded from the USB is still on the HDD.

    The archive receipt deliberately records metadata rather than a second copy
    of each file hash.  Its generation is tied to the governed zero-mismatch
    verification and cutover receipt below; this check confirms the recorded
    durable path set remains present at the canonical destination.
    """
    rows = archive.get("relative_path_manifest")
    if not isinstance(rows, list) or not rows:
        raise ValueError("USB archive receipt has no durable relative-path manifest.")
    primary = config.primary.mount_path.resolve()
    files = bytes_total = entries = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("USB archive receipt contains an invalid manifest row.")
        rel = _safe_relative_path(row.get("path"))
        target = primary / rel
        try:
            target.relative_to(primary)
        except ValueError as exc:  # defensive, even after lexical validation
            raise ValueError(f"USB archive path escapes the primary HDD: {rel}") from exc
        kind = row.get("kind")
        if kind == "dir":
            valid = target.is_dir() and not target.is_symlink()
        elif kind == "file":
            size = row.get("size")
            valid = target.is_file() and not target.is_symlink() and (
                not isinstance(size, int) or target.stat().st_size == size
            )
            if valid:
                files += 1
                bytes_total += target.stat().st_size
        elif kind == "symlink":
            valid = target.is_symlink()
        else:
            raise ValueError(f"USB archive receipt has unsupported entry kind for {rel}: {kind!r}")
        if not valid:
            raise ValueError(f"Historical USB archive entry is missing or changed on primary HDD: {rel}")
        entries += 1
    return entries, files, bytes_total


def retire_legacy_usb_archive(*, confirmation: str, config: StorageConfig | None = None) -> Path:
    """Record the one-time, non-destructive retirement of the legacy USB.

    This transition only creates immutable evidence.  It intentionally does
    not edit local configuration, host-maintenance state, mount state, or USB
    contents.  The USB may already be offline: its recorded archive manifest
    and completed cutover evidence are the authority in that state.
    """
    if confirmation.strip() != CONFIRMATION:
        raise ValueError(f"Type {CONFIRMATION} to retire the legacy USB archive.")
    cfg = config or load_storage_config(warn_deprecated=False)
    if (
        cfg.active_write_role != StorageWriteRole.PRIMARY
        or cfg.migration_state != MigrationState.CUTOVER_COMPLETE
        or cfg.legacy.role != StorageRootRole.LEGACY_ARCHIVE
        or cfg.legacy.writable
    ):
        raise ValueError("Storage configuration is not a completed primary-writer cutover with a read-only legacy archive.")
    status = build_storage_status_report()
    if not status.primary.validation.ok:
        raise ValueError("Primary HDD mount/identity validation failed; USB retirement was not recorded.")
    active = detect_active_operations()
    if active:
        raise ValueError("Active operation blocks legacy USB retirement: " + ", ".join(active))
    host = load_host_maintenance()
    if not writes_allowed(host) or host.active_write_role != StorageWriteRole.PRIMARY.value:
        raise ValueError("Host maintenance does not authorize the active primary Mercury writer.")
    cutover = read_cutover_receipt(config=cfg)
    generation = read_verified_generation(config=cfg)
    archive = read_archive_receipt(config=cfg)
    if not cutover or not generation or not archive:
        raise ValueError("Required cutover, generation, or USB archive evidence is missing.")
    if (
        cutover.get("hdd_uuid") != cfg.primary.filesystem_uuid
        or cutover.get("usb_uuid") != cfg.legacy.filesystem_uuid
        or archive.get("usb_uuid") != cfg.legacy.filesystem_uuid
    ):
        raise ValueError("Cutover/archive receipt storage UUIDs do not match configured storage identities.")
    if (
        cutover.get("cutover_verified_hdd_generation") != generation
        or cutover.get("final_usb_archive_generation") != generation
        or archive.get("final_usb_archive_generation") != generation
    ):
        raise ValueError("Zero-mismatch USB-to-HDD generation evidence is missing or inconsistent.")
    receipt_path = cfg.primary.control_dir / RETIREMENT_RECEIPT_FILE
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError(f"Legacy USB retirement is already recorded: {receipt_path}")
    entries, files, bytes_total = _verify_historical_archive_on_primary(config=cfg, archive=archive)
    transition_id = new_transition_id()
    receipt = {
        "decision": "LEGACY_USB_RETIRED_ARCHIVE_ONLY",
        "retired_at_utc": datetime.now(timezone.utc).isoformat(),
        "transition_id": transition_id,
        "legacy_usb": {
            "uuid": cfg.legacy.filesystem_uuid,
            "label": cfg.legacy.label,
            "mount_path": str(cfg.legacy.mount_path),
            "configured_role": cfg.legacy.role.value,
            "configured_writable": cfg.legacy.writable,
            "physical_state_at_retirement": "offline" if status.legacy.is_offline_archive else "mounted_archive",
        },
        "primary_hdd": {
            "uuid": cfg.primary.filesystem_uuid,
            "mount_path": str(cfg.primary.mount_path),
            "active_writer": True,
            "mount_validation": status.primary.validation.code.value,
        },
        "zero_mismatch_evidence": {
            "migration_state": cfg.migration_state.value,
            "verified_generation": generation,
            "cutover_receipt": str(cfg.primary.control_dir / "cutover_receipt.json"),
            "archive_receipt": str(cfg.primary.control_dir / "usb_archive_receipt.json"),
            "archive_manifest_sha256": archive.get("manifest_sha256"),
        },
        "historical_archive_on_primary": {
            "entries_verified": entries,
            "files_verified": files,
            "bytes_verified": bytes_total,
        },
        "host_maintenance": asdict(host),
        "local_configuration_changed": False,
        "host_maintenance_changed": False,
        "mounts_changed": False,
        "usb_data_changed": False,
        "normal_validation_dependency": "none; legacy USB is an optional offline recovery archive",
    }
    written = write_immutable_receipt(RETIREMENT_RECEIPT_FILE, receipt, config=cfg)
    append_transition_ledger(
        {
            "transition": "legacy_usb_archive_retirement",
            "transition_id": transition_id,
            "result": "SUCCESS",
            "receipt": str(written),
            "hdd_uuid": cfg.primary.filesystem_uuid,
            "usb_uuid": cfg.legacy.filesystem_uuid,
            "historical_entries_verified": entries,
            "historical_files_verified": files,
            "historical_bytes_verified": bytes_total,
            "usb_data_changed": False,
            "mounts_changed": False,
        }
    )
    return written
