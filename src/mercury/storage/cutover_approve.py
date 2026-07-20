"""Guarded writer cutover from verified USB package to canonical HDD."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import re
import subprocess

from mercury.core.paths import LOCAL_CONFIG
from mercury.core.storage_roles import MigrationState, StorageWriteRole
from mercury.core.storage_roots import load_storage_config
from mercury.migration.generation import CUTOVER_RECEIPT_FILE, build_usb_generation, read_verified_generation, write_immutable_receipt
from mercury.storage.cutover_readiness import build_cutover_readiness

CONFIRMATION = "USE HDD WRITER"


def _mercury_revision() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def approve_hdd_writer_cutover(*, confirmation: str, local_config: Path | None = None) -> Path:
    """Atomically select the verified primary HDD; preserves a rollback config copy."""
    if confirmation.strip() != CONFIRMATION:
        raise ValueError(f"Type {CONFIRMATION} to approve HDD writer cutover.")
    path = local_config or LOCAL_CONFIG
    config = load_storage_config(local_config=path, warn_deprecated=False)
    readiness = build_cutover_readiness(local_config=path, config=config)
    generation = build_usb_generation(config=config)
    if not readiness.ready:
        raise ValueError("Cutover readiness is not satisfied.")
    if read_verified_generation(config=config) != generation.generation:
        raise ValueError("HDD final package generation is stale or unrecorded; synchronize and verify first.")
    if config.active_write_role != StorageWriteRole.LEGACY:
        raise ValueError("Active writer is not USB legacy; refusing repeated cutover.")
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
    updated = re.sub(r'(?m)^(\s*active_write_role\s*=\s*)"[^"]*"', r'\g<1>"primary"', updated, count=1)
    updated = re.sub(r'(?m)^(\s*migration_state\s*=\s*)"[^"]*"', r'\g<1>"cutover_complete"', updated, count=1)
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
    }
    try:
        write_immutable_receipt(CUTOVER_RECEIPT_FILE, receipt, config=post)
    except Exception:
        # Cutover evidence is mandatory for a new cutover.  Restore the exact
        # config backup rather than leave a writer switch without its receipt.
        backup.replace(path)
        raise
    return backup
