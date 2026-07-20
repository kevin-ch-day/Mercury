"""Package generations and immutable evidence for the storage cutover.

Before cutover, the source of a package is the legacy USB.  After cutover the
HDD is authoritative.  These are deliberately separate concepts: routine
status must never reinterpret an archived USB as the live package.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from mercury.core.storage_roles import CONTROL_DIRNAME
from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.storage.migrate_plan import EntryKind, is_ephemeral_relative, is_excluded_relative, iter_source_entries

GENERATION_FILE = "final_package_generation.json"  # historical compatibility record
CUTOVER_RECEIPT_FILE = "cutover_receipt.json"
ARCHIVE_RECEIPT_FILE = "usb_archive_receipt.json"


@dataclass(frozen=True)
class PackageGeneration:
    generation: str
    observed_at: str
    durable_entries: int
    durable_files: int
    latest_package_timestamp: float | None

    def payload(self) -> dict[str, object]:
        return {
            "generation": self.generation, "observed_at": self.observed_at,
            "durable_entries": self.durable_entries, "durable_files": self.durable_files,
            "latest_package_timestamp": self.latest_package_timestamp,
        }


def build_generation(root: Path, *, mercury_managed_only: bool = False) -> PackageGeneration:
    """Fingerprint durable Mercury-managed entries rooted at ``root``.

    Ephemeral log/state trees and the primary-only control directory are
    excluded.  Directory timestamps are intentionally not included.
    """
    digest = hashlib.sha256(); entries = files = 0; newest: float | None = None
    for rel, path, kind in iter_source_entries(root):
        if is_excluded_relative(rel) or is_ephemeral_relative(rel):
            continue
        if mercury_managed_only and not Path(rel).parts[0].startswith("mercury_"):
            continue
        stat = path.lstat(); entries += 1
        if kind == EntryKind.DIR:
            digest.update(f"{kind.value}\0{rel}\n".encode())
            continue
        if kind == EntryKind.FILE:
            files += 1
        newest = max(newest or stat.st_mtime, stat.st_mtime)
        # File metadata is an efficient durable-artifact identity; full content
        # equality remains the explicit storage SHA-256 audit operation.
        digest.update(f"{kind.value}\0{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return PackageGeneration(digest.hexdigest(), datetime.now(timezone.utc).isoformat(), entries, files, newest)


def build_usb_generation(*, config: StorageConfig | None = None) -> PackageGeneration:
    cfg = config or load_storage_config(warn_deprecated=False)
    return build_generation(cfg.legacy.mount_path)


def build_active_hdd_generation(*, config: StorageConfig | None = None) -> PackageGeneration:
    """Return the active post-cutover package generation from the HDD."""
    cfg = config or load_storage_config(warn_deprecated=False)
    # HDD-only ScytaleDroid stores and unrelated external-intake roots are not
    # Mercury package artifacts and must not churn package identity.
    return build_generation(cfg.primary.mount_path, mercury_managed_only=True)


def control_path(filename: str, *, config: StorageConfig | None = None) -> Path:
    cfg = config or load_storage_config(warn_deprecated=False)
    return cfg.primary.mount_path / CONTROL_DIRNAME / filename


def generation_record_path(*, config: StorageConfig | None = None) -> Path:
    return control_path(GENERATION_FILE, config=config)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def read_verified_generation(*, config: StorageConfig | None = None) -> str | None:
    """Read the historical pre-cutover USB→HDD verification generation."""
    record = _read_json(generation_record_path(config=config))
    return str(record["generation"]) if record and record.get("generation") else None


def record_verified_generation(generation: PackageGeneration, *, config: StorageConfig | None = None) -> Path:
    """Record pre-cutover final-mirror evidence (not used as live HDD status)."""
    path = generation_record_path(config=config); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(generation.payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_cutover_receipt(*, config: StorageConfig | None = None) -> dict[str, Any] | None:
    return _read_json(control_path(CUTOVER_RECEIPT_FILE, config=config))


def read_archive_receipt(*, config: StorageConfig | None = None) -> dict[str, Any] | None:
    return _read_json(control_path(ARCHIVE_RECEIPT_FILE, config=config))


def write_immutable_receipt(filename: str, payload: dict[str, Any], *, config: StorageConfig | None = None, override: bool = False) -> Path:
    """Write an evidence receipt once; explicit override is intentionally rare."""
    path = control_path(filename, config=config)
    if path.is_symlink():
        raise ValueError(f"Refusing receipt path symlink: {path}")
    if path.exists() and not override:
        raise ValueError(f"Historical receipt already exists: {path}. Use an explicit administrative override to replace it.")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if not override:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ValueError(
                f"Historical receipt already exists: {path}. Use an explicit administrative override to replace it."
            ) from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return path

    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return path
