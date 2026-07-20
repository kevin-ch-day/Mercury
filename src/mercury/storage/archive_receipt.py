"""Receipt for the USB recovery archive retained after HDD cutover."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.migration.generation import ARCHIVE_RECEIPT_FILE, build_usb_generation, read_verified_generation, write_immutable_receipt
from mercury.storage.migrate_plan import is_ephemeral_relative, is_excluded_relative, iter_source_entries


@dataclass(frozen=True)
class ArchiveReceiptResult:
    payload: dict[str, Any]
    path: Path
    executed: bool


def _mount_mode(path: Path) -> str:
    """Return the actual mount mode, never an application-policy label."""
    try:
        target = str(path.resolve())
        candidates: list[tuple[int, str]] = []
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if "-" not in fields or len(fields) < 6:
                continue
            mount = fields[4].replace("\\040", " ")
            if target == mount or target.startswith(mount.rstrip("/") + "/"):
                candidates.append((len(mount), fields[5]))
        if candidates:
            options = max(candidates)[1].split(",")
            return "read-only" if "ro" in options else "read-write"
    except OSError:
        pass
    return "unknown"


def _device_metadata(path: Path) -> tuple[str | None, str | None]:
    try:
        completed = subprocess.run(["findmnt", "-no", "UUID,LABEL", "--target", str(path)], check=True, capture_output=True, text=True)
        values = completed.stdout.strip().split(maxsplit=1)
        return (values[0] if values else None, values[1] if len(values) > 1 else None)
    except (OSError, subprocess.CalledProcessError):
        return None, None


def _relative_manifest(root: Path) -> tuple[list[dict[str, object]], str]:
    rows: list[dict[str, object]] = []
    for rel, path, kind in iter_source_entries(root):
        if is_excluded_relative(rel) or is_ephemeral_relative(rel):
            continue
        stat = path.lstat()
        rows.append({"path": rel, "kind": kind.value, "size": stat.st_size if kind.value != "dir" else None, "mtime_ns": stat.st_mtime_ns if kind.value != "dir" else None})
    encoded = ("\n".join(__import__("json").dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n").encode()
    return rows, hashlib.sha256(encoded).hexdigest()


def build_archive_receipt(*, config: StorageConfig | None = None) -> ArchiveReceiptResult:
    cfg = config or load_storage_config(warn_deprecated=False)
    generation = build_usb_generation(config=cfg)
    rows, manifest_sha = _relative_manifest(cfg.legacy.mount_path)
    uuid, label = _device_metadata(cfg.legacy.mount_path)
    payload: dict[str, Any] = {
        "archive_timestamp": datetime.now(timezone.utc).isoformat(),
        "usb_uuid": uuid or cfg.legacy.filesystem_uuid,
        "usb_label": label or cfg.legacy.label,
        "final_usb_archive_generation": read_verified_generation(config=cfg) or generation.generation,
        "observed_usb_generation": generation.generation,
        "durable_file_count": generation.durable_files,
        "durable_entry_count": generation.durable_entries,
        "relative_path_manifest": rows,
        "manifest_sha256": manifest_sha,
        "filesystem_mount_mode": _mount_mode(cfg.legacy.mount_path),
        "application_policy_role": cfg.legacy.role.value,
        "application_policy": "archive-only" if cfg.cutover_complete else "transition-source",
        "physical_retirement": "not_authorized",
    }
    return ArchiveReceiptResult(payload, cfg.primary.control_dir / ARCHIVE_RECEIPT_FILE, False)


def record_archive_receipt(*, config: StorageConfig | None = None, override: bool = False) -> ArchiveReceiptResult:
    result = build_archive_receipt(config=config)
    path = write_immutable_receipt(ARCHIVE_RECEIPT_FILE, result.payload, config=config, override=override)
    return ArchiveReceiptResult(result.payload, path, True)
