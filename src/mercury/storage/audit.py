"""Read-only storage audit, including an opt-in byte-level comparison.

This complements :mod:`mercury.storage.migrate_verify`: migration verification
uses size and mtime so it can run quickly, while this module can additionally
hash every legacy file against its corresponding primary file.  It never copies
payloads, changes migration state, or switches writers. Invoke the CLI with
``--no-logging`` when a strictly payload-and-log read-only audit is required.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from mercury.core.paths import OUTPUT_DIR
from mercury.core.storage_roots import load_storage_config
from mercury.storage.migrate_plan import EntryKind, is_ephemeral_relative, iter_source_entries
from mercury.storage.migrate_verify import MigrationVerifyReport, verify_migration


@dataclass(frozen=True)
class HashDifference:
    """One byte-level comparison that did not match."""

    relative_path: str
    issue: str
    ephemeral: bool


@dataclass(frozen=True)
class StorageAuditReport:
    """Combined metadata verification and optional SHA-256 result."""

    verification: MigrationVerifyReport
    hashes_requested: bool = False
    files_hashed: int = 0
    identical_hashes: int = 0
    differences: tuple[HashDifference, ...] = ()
    primary_mount_targets: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    post_cutover: bool = False

    @property
    def durable_differences(self) -> tuple[HashDifference, ...]:
        return tuple(item for item in self.differences if not item.ephemeral)

    @property
    def ephemeral_differences(self) -> tuple[HashDifference, ...]:
        return tuple(item for item in self.differences if item.ephemeral)

    @property
    def ok(self) -> bool:
        return self.verification.ok and not self.durable_differences

    @property
    def exit_code(self) -> int:
        """0 clean, 1 completed with warnings, 2 integrity/configuration blocker."""
        if not self.ok:
            return 2
        if self.warnings or self.verification.warnings or self.ephemeral_differences:
            return 1
        return 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_compare_trees(source: Path, destination: Path) -> tuple[int, int, tuple[HashDifference, ...]]:
    """Compare every regular source file with the same relative primary path."""
    files_hashed = identical = 0
    differences: list[HashDifference] = []
    for relative_path, source_path, kind in iter_source_entries(source):
        if kind != EntryKind.FILE:
            continue
        target = destination / relative_path
        ephemeral = is_ephemeral_relative(relative_path)
        if not target.is_file() or target.is_symlink():
            differences.append(HashDifference(relative_path, "missing_or_not_file", ephemeral))
            continue
        try:
            before = source_path.stat()
            source_hash = _sha256(source_path)
            target_hash = _sha256(target)
            after = source_path.stat()
        except OSError:
            differences.append(HashDifference(relative_path, "unreadable", ephemeral))
            continue
        files_hashed += 1
        if source_hash == target_hash:
            identical += 1
        else:
            differences.append(HashDifference(relative_path, "sha256_mismatch", ephemeral))
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            differences.append(HashDifference(relative_path, "source_changed_during_hash", ephemeral))
    return files_hashed, identical, tuple(differences)


def mount_targets_from_findmnt_output(text: str) -> tuple[str, ...]:
    """Normalize ``findmnt -o TARGET`` output without trusting device names."""
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def find_mount_targets(filesystem_uuid: str) -> tuple[str, ...]:
    """Return all currently visible mounts for a filesystem UUID, best effort."""
    try:
        result = subprocess.run(
            ["findmnt", "-rn", "-S", f"UUID={filesystem_uuid}", "-o", "TARGET"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ()
    return mount_targets_from_findmnt_output(result.stdout)


def build_storage_audit(*, hash_files: bool = False) -> StorageAuditReport:
    """Build an observe-only configured-root audit."""
    cfg = load_storage_config(warn_deprecated=False)
    verification = verify_migration(config=cfg)
    mount_targets = find_mount_targets(cfg.primary.filesystem_uuid)
    warnings: list[str] = []
    if len(mount_targets) > 1:
        warnings.append(
            "Primary filesystem is mounted at multiple paths: " + ", ".join(mount_targets)
        )
    if not hash_files or not verification.ok:
        return StorageAuditReport(
            verification=verification,
            hashes_requested=hash_files,
            primary_mount_targets=mount_targets,
            warnings=tuple(warnings),
            post_cutover=cfg.cutover_complete,
        )
    files_hashed, identical, differences = hash_compare_trees(
        Path(verification.source_mount), Path(verification.dest_mount)
    )
    return StorageAuditReport(
        verification=verification,
        hashes_requested=True,
        files_hashed=files_hashed,
        identical_hashes=identical,
        differences=differences,
        primary_mount_targets=mount_targets,
        warnings=tuple(warnings),
        post_cutover=cfg.cutover_complete,
    )


def default_storage_audit_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return OUTPUT_DIR / "storage" / f"storage_audit_{stamp}.json"


def write_storage_audit_report(report: StorageAuditReport, path: Path | None = None) -> Path:
    """Write a JSON audit artifact under Mercury output (never onto either volume)."""
    target = path or default_storage_audit_report_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A completed audit report is evidence.  Replace it atomically so Ctrl-C or
    # a full filesystem never leaves a plausible-looking partial JSON document.
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(target)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return target
