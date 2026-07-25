"""Guarded writer cutover from verified USB package to canonical HDD."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import re
import subprocess

from mercury.core.paths import resolve_local_config
from mercury.core.storage_roles import MigrationState, StorageWriteRole
from mercury.core.storage_roots import load_storage_config
from mercury.migration.generation import CUTOVER_RECEIPT_FILE, build_usb_generation, read_verified_generation, write_immutable_receipt
from mercury.storage.cutover_readiness import build_cutover_readiness
from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    load_host_maintenance,
    save_host_maintenance,
    writes_allowed,
)
from mercury.storage.transitions import append_transition_ledger, detect_active_operations, new_transition_id

CONFIRMATION = "USE HDD WRITER"


def _mercury_revision() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _replace_or_add_storage_value(content: str, key: str, value: str) -> str:
    """Set a quoted [storage] value, creating it for partial destination configs."""
    pattern = rf"(?m)^(\s*{re.escape(key)}\s*=\s*)\"[^\"]*\""
    if re.search(pattern, content):
        return re.sub(pattern, r'\g<1>"' + value + '"', content, count=1)
    section = re.compile(r"(?m)^\[storage\]\s*$")
    match = section.search(content)
    if not match:
        suffix = "" if not content or content.endswith("\n") else "\n"
        return f"{content}{suffix}\n[storage]\n{key} = \"{value}\"\n"
    insert_at = match.end()
    return f"{content[:insert_at]}\n{key} = \"{value}\"{content[insert_at:]}"


def _post_cutover_host_state(previous: HostMaintenanceState) -> HostMaintenanceState:
    """Return the only host-maintenance state that authorizes the new HDD writer."""
    return replace(
        previous,
        storage_availability="mounted",
        writes_allowed=True,
        active_write_role=StorageWriteRole.PRIMARY.value,
        source_detach_preparation=False,
        destination_rehearsal_active=False,
        destination_rehearsal_in_progress=False,
        destination_rehearsal_planned=True,
        intentional_safe_disconnect=False,
        last_safe_disconnect_result="",
        notes=(
            "Governed destination writer cutover complete; the verified primary HDD "
            "is the only Mercury writer. Legacy USB is retained read-only as archive."
        ),
    )


def _validate_post_cutover_host_state(state: HostMaintenanceState) -> None:
    if state.storage_availability not in {"mounted", "attached"}:
        raise ValueError(f"post-cutover storage_availability={state.storage_availability!r}")
    if not writes_allowed(state):
        raise ValueError("post-cutover host maintenance still refuses primary writes")
    if state.active_write_role != StorageWriteRole.PRIMARY.value:
        raise ValueError("post-cutover host maintenance role is not primary")
    if state.source_detach_preparation or state.destination_rehearsal_in_progress:
        raise ValueError("post-cutover host maintenance still indicates an active rehearsal")


def approve_hdd_writer_cutover(*, confirmation: str, local_config: Path | None = None) -> Path:
    """Atomically select the verified primary HDD; preserves a rollback config copy."""
    if confirmation.strip() != CONFIRMATION:
        raise ValueError(f"Type {CONFIRMATION} to approve HDD writer cutover.")
    path = local_config or resolve_local_config()
    config = load_storage_config(local_config=path, warn_deprecated=False)
    readiness = build_cutover_readiness(local_config=path, config=config)
    generation = build_usb_generation(config=config)
    if not readiness.ready:
        raise ValueError("Cutover readiness is not satisfied.")
    if read_verified_generation(config=config) != generation.generation:
        raise ValueError("HDD final package generation is stale or unrecorded; synchronize and verify first.")
    if config.active_write_role != StorageWriteRole.LEGACY:
        raise ValueError("Active writer is not USB legacy; refusing repeated cutover.")
    host_before = load_host_maintenance()
    if writes_allowed(host_before) or host_before.active_write_role != "none":
        raise ValueError("Host maintenance must keep writers disabled with active_write_role=none before cutover.")
    active_operations = detect_active_operations()
    if active_operations:
        raise ValueError("Active operation blocks cutover: " + ", ".join(active_operations))
    original = path.read_text(encoding="utf-8")
    backup = path.with_name(path.name + ".pre_hdd_cutover")
    if backup.exists():
        raise ValueError(f"Rollback config already exists: {backup}")
    replacements = {
        "backup_root": str(config.primary.backup_root), "log_dir": str(config.primary.log_dir),
        "repo_backup_root": str(config.primary.repo_backup_root), "manifest_dir": str(config.primary.manifest_dir),
        "runbook_dir": str(config.primary.runbook_dir),
    }
    updated = original
    for key, value in replacements.items():
        updated = re.sub(rf'(?m)^(\s*{re.escape(key)}\s*=\s*)"[^"]*"', rf'\g<1>"{value}"', updated, count=1)
    updated = _replace_or_add_storage_value(updated, "active_write_role", "primary")
    updated = _replace_or_add_storage_value(updated, "migration_state", "cutover_complete")
    updated = re.sub(r'(?ms)(\[storage\.legacy\].*?^\s*role\s*=\s*)"[^"]*"', r'\g<1>"legacy_archive"', updated, count=1)
    updated = re.sub(r'(?ms)(\[storage\.legacy\].*?^\s*writable\s*=\s*)\w+', r'\g<1>false', updated, count=1)
    backup.write_text(original, encoding="utf-8")
    temp = path.with_suffix(path.suffix + ".cutover.tmp")
    temp.write_text(updated, encoding="utf-8")
    temp.replace(path)
    post = load_storage_config(local_config=path, warn_deprecated=False)
    if post.active_write_role != StorageWriteRole.PRIMARY or post.migration_state != MigrationState.CUTOVER_COMPLETE:
        backup.replace(path)
        raise ValueError("Post-cutover validation failed; restored prior config.")
    host_after = _post_cutover_host_state(host_before)
    transition_id = new_transition_id()
    try:
        save_host_maintenance(host_after)
        _validate_post_cutover_host_state(load_host_maintenance())
    except Exception as exc:
        save_host_maintenance(host_before)
        backup.replace(path)
        raise ValueError("Post-cutover host-maintenance validation failed; restored prior config and maintenance state.") from exc
    receipt = {
        "cutover_timestamp": datetime.now(timezone.utc).isoformat(),
        "old_active_role": StorageWriteRole.LEGACY.value,
        "new_active_role": StorageWriteRole.PRIMARY.value,
        "final_usb_archive_generation": generation.generation,
        "cutover_verified_hdd_generation": generation.generation,
        "usb_uuid": config.legacy.filesystem_uuid,
        "hdd_uuid": config.primary.filesystem_uuid,
        "pre_cutover_config_backup_path": str(backup),
        "post_cutover_configuration_sha256": hashlib.sha256(updated.encode()).hexdigest(),
        "verification_artifact_path": str(config.primary.control_dir / "final_package_generation.json"),
        "mercury_commit": _mercury_revision(),
        "transition_id": transition_id,
        "host_maintenance_before": {
            "storage_availability": host_before.storage_availability,
            "writes_allowed": writes_allowed(host_before),
            "active_write_role": host_before.active_write_role,
        },
        "host_maintenance_after": {
            "storage_availability": host_after.storage_availability,
            "writes_allowed": writes_allowed(host_after),
            "active_write_role": host_after.active_write_role,
        },
    }
    try:
        write_immutable_receipt(CUTOVER_RECEIPT_FILE, receipt, config=post)
        append_transition_ledger(
            {
                "transition": "destination_writer_cutover",
                "transition_id": transition_id,
                "result": "SUCCESS",
                "old_active_role": StorageWriteRole.LEGACY.value,
                "new_active_role": StorageWriteRole.PRIMARY.value,
                "migration_state": post.migration_state.value,
                "receipt": str(post.primary.control_dir / CUTOVER_RECEIPT_FILE),
                "usb_uuid": config.legacy.filesystem_uuid,
                "hdd_uuid": config.primary.filesystem_uuid,
            }
        )
    except Exception:
        # Cutover evidence is mandatory for a new cutover.  Restore the exact
        # config and host-maintenance state rather than leave a writer switch
        # without complete governed evidence.
        save_host_maintenance(host_before)
        backup.replace(path)
        raise
    return backup
