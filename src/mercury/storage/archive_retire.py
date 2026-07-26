"""Governed retirement of the post-cutover legacy USB as an offline archive."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from mercury.core.paths import resolve_local_config
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
LEGACY_RUNTIME_DEPENDENCY_NONE = "none"


@dataclass(frozen=True)
class LegacyArchiveStatus:
    """Informational view of the retired USB; never a readiness input."""

    phased_out: bool
    uuid: str
    label: str
    mount_path: str
    connected: bool
    mount_mode: str  # offline | read-only | read-write | unknown
    operator_line: str
    retirement_receipt: Path | None


def _safe_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("USB archive receipt contains an invalid relative path.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"USB archive receipt contains an unsafe path: {value!r}")
    return path


def retirement_receipt_path(*, config: StorageConfig | None = None) -> Path:
    cfg = config or load_storage_config(warn_deprecated=False)
    return cfg.primary.control_dir / RETIREMENT_RECEIPT_FILE


def read_retirement_receipt(*, config: StorageConfig | None = None) -> dict[str, Any] | None:
    path = retirement_receipt_path(config=config)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def legacy_usb_is_phased_out(*, config: StorageConfig | None = None) -> bool:
    """True when the governed retirement receipt is recorded for a completed cutover."""
    cfg = config or load_storage_config(warn_deprecated=False)
    if (
        cfg.active_write_role != StorageWriteRole.PRIMARY
        or cfg.migration_state != MigrationState.CUTOVER_COMPLETE
        or cfg.legacy.role != StorageRootRole.LEGACY_ARCHIVE
    ):
        return False
    receipt = read_retirement_receipt(config=cfg)
    if not receipt:
        return False
    return receipt.get("decision") == "LEGACY_USB_RETIRED_ARCHIVE_ONLY"


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


def _assert_retirement_prerequisites(cfg: StorageConfig) -> tuple[dict[str, Any], str, dict[str, Any], Any]:
    if (
        cfg.active_write_role != StorageWriteRole.PRIMARY
        or cfg.migration_state != MigrationState.CUTOVER_COMPLETE
        or cfg.legacy.role != StorageRootRole.LEGACY_ARCHIVE
        or cfg.legacy.writable
    ):
        raise ValueError(
            "Storage configuration is not a completed primary-writer cutover with a read-only legacy archive."
        )
    # Prefer the caller-supplied config for identity checks; status report is
    # only used for primary mount health and physical archive state.
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
    return cutover, generation, archive, status


def _replace_or_add_storage_value(content: str, key: str, value: str) -> str:
    pattern = rf"(?m)^(\s*{re.escape(key)}\s*=\s*)\"[^\"]*\""
    if re.search(pattern, content):
        return re.sub(pattern, r'\g<1>"' + value + '"', content, count=1)
    section = re.compile(r"(?m)^\[storage\]\s*$")
    match = section.search(content)
    if not match:
        suffix = "" if not content or content.endswith("\n") else "\n"
        return f'{content}{suffix}\n[storage]\n{key} = "{value}"\n'
    insert_at = match.end()
    return f'{content[:insert_at]}\n{key} = "{value}"{content[insert_at:]}'


def apply_legacy_usb_runtime_policy(*, config: StorageConfig | None = None) -> bool:
    """Remove legacy USB from active runtime policy in local.toml. Returns True if edited."""
    cfg = config or load_storage_config(warn_deprecated=False)
    path = resolve_local_config()
    if path is None or not path.is_file():
        raise ValueError("config/local.toml is required to apply legacy USB runtime policy.")
    text = path.read_text(encoding="utf-8")
    updated = _replace_or_add_storage_value(text, "legacy_runtime_dependency", LEGACY_RUNTIME_DEPENDENCY_NONE)
    # Keep archive identity, but never leave a writable legacy policy after phase-out.
    if 'role = "transition_source"' in updated:
        updated = updated.replace('role = "transition_source"', 'role = "legacy_archive"', 1)
    if re.search(
        r'(?ms)^\[storage\.legacy\].*?^writable\s*=\s*true\s*$',
        updated,
    ):
        updated = re.sub(
            r'(?ms)(^\[storage\.legacy\].*?^writable\s*=\s*)true(\s*$)',
            r"\1false\2",
            updated,
            count=1,
        )
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def build_legacy_archive_status(*, config: StorageConfig | None = None) -> LegacyArchiveStatus:
    """Describe retired-USB presence for archive commands / optional detail only."""
    cfg = config or load_storage_config(warn_deprecated=False)
    phased = legacy_usb_is_phased_out(config=cfg)
    receipt = retirement_receipt_path(config=cfg) if phased else None
    status = build_storage_status_report()
    legacy = status.legacy
    connected = bool(legacy.validation.identity.is_mount)
    if not connected:
        mount_mode = "offline"
        line = "retired / offline" if phased else "offline"
    else:
        mount_mode = legacy.physical_mount_mode
        if phased and mount_mode == "read-only":
            line = "retired · connected read-only"
        elif phased and mount_mode == "read-write":
            line = "retired · connected writable (not a writer)"
        elif phased:
            line = f"retired · connected ({mount_mode})"
        else:
            line = f"mounted ({mount_mode})"
    return LegacyArchiveStatus(
        phased_out=phased,
        uuid=cfg.legacy.filesystem_uuid,
        label=cfg.legacy.label,
        mount_path=str(cfg.legacy.mount_path),
        connected=connected,
        mount_mode=mount_mode,
        operator_line=line,
        retirement_receipt=receipt if receipt and receipt.is_file() else None,
    )


def retire_legacy_usb_archive(*, confirmation: str, config: StorageConfig | None = None) -> Path:
    """Record retirement evidence and remove USB from active runtime policy.

    Idempotent: a valid existing retirement receipt is re-validated, runtime
    policy is applied if needed, and the same receipt path is returned.
    Never modifies USB contents or mount state.
    """
    if confirmation.strip() != CONFIRMATION:
        raise ValueError(f"Type {CONFIRMATION} to retire the legacy USB archive.")
    cfg = config or load_storage_config(warn_deprecated=False)
    cutover, generation, archive, status = _assert_retirement_prerequisites(cfg)
    receipt_path = cfg.primary.control_dir / RETIREMENT_RECEIPT_FILE
    config_changed = apply_legacy_usb_runtime_policy(config=cfg)

    if receipt_path.exists() or receipt_path.is_symlink():
        existing = read_retirement_receipt(config=cfg)
        if not existing or existing.get("decision") != "LEGACY_USB_RETIRED_ARCHIVE_ONLY":
            raise ValueError(f"Legacy USB retirement receipt is unreadable or invalid: {receipt_path}")
        if existing.get("legacy_usb", {}).get("uuid") != cfg.legacy.filesystem_uuid:
            raise ValueError("Existing retirement receipt UUID does not match configured legacy USB.")
        if config_changed:
            append_transition_ledger(
                {
                    "transition": "legacy_usb_runtime_policy_apply",
                    "transition_id": new_transition_id(),
                    "result": "SUCCESS",
                    "receipt": str(receipt_path),
                    "hdd_uuid": cfg.primary.filesystem_uuid,
                    "usb_uuid": cfg.legacy.filesystem_uuid,
                    "local_configuration_changed": True,
                    "usb_data_changed": False,
                    "mounts_changed": False,
                }
            )
        return receipt_path

    entries, files, bytes_total = _verify_historical_archive_on_primary(config=cfg, archive=archive)
    transition_id = new_transition_id()
    host = load_host_maintenance()
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
            "mismatches": 0,
        },
        "historical_archive_on_primary": {
            "entries_verified": entries,
            "files_verified": files,
            "bytes_verified": bytes_total,
        },
        "host_maintenance": asdict(host),
        "local_configuration_changed": config_changed,
        "host_maintenance_changed": False,
        "mounts_changed": False,
        "usb_data_changed": False,
        "normal_validation_dependency": "none; legacy USB is an optional offline recovery archive",
        "legacy_runtime_dependency": LEGACY_RUNTIME_DEPENDENCY_NONE,
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
            "mismatches": 0,
            "usb_data_changed": False,
            "mounts_changed": False,
            "local_configuration_changed": config_changed,
        }
    )
    return written
