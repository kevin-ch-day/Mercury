"""Pin deployment inputs and import a private, reverified copy of those bytes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from mercury.backup.checksum import parse_checksum_file
from mercury.backup.manifest import BackupManifest
from mercury.backup.verification import verify_backup_artifacts
from mercury.deploy.models import DeploymentCandidate


def _direct_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Deployment inputs must not contain traversal or symlinks")
    if path.resolve(strict=True) != path:
        raise ValueError("Deployment input is not a canonical path")
    return path


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_regular(path: Path, *, destination: Path | None = None, limit: int | None = None):
    path = _direct_path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Deployment input must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    data = bytearray()
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or (limit is not None and before.st_size > limit):
            raise ValueError("Deployment input must be a bounded regular file")
        output = destination.open("xb") if destination is not None else None
        try:
            size = 0
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                if limit is not None and size > limit:
                    raise ValueError("Deployment metadata exceeds its size limit")
                digest.update(chunk)
                if output is not None:
                    output.write(chunk)
                else:
                    data.extend(chunk)
        finally:
            if output is not None:
                output.close()
        _direct_path(path)  # Ancestors must still be direct, not redirected links.
        if _identity(before) != _identity(os.fstat(source.fileno())) or _identity(before) != _identity(path.lstat()):
            raise ValueError("Deployment input changed while being captured")
    if destination is not None:
        destination.chmod(0o400)
    return bytes(data), digest.hexdigest()


def pin_deployment_artifacts(
    directory: Path, *, database: str, backup_id: str,
    allow_development_backup: bool = False,
) -> dict[str, str]:
    """Record metadata hashes after selection; execution rechecks all data bytes."""
    directory = _direct_path(directory)
    raw, manifest_digest = _read_regular(directory / "manifest.json", limit=8 * 1024 * 1024)
    manifest = BackupManifest.model_validate(json.loads(raw))
    from mercury.database.core import classify_database

    classification = classify_database(database)
    if not classification.backup_source and not (allow_development_backup and classification.dev_target):
        raise ValueError("Selected database is not authorized for this artifact lane")
    if manifest.database != database or manifest.backup_id != backup_id or manifest.backup_kind != "full":
        raise ValueError("Selected backup identity does not match the manifest")
    if manifest.object_scope != "complete_schema":
        raise ValueError("Partial database artifacts cannot authorize deployment")
    from mercury.backup.content_contract import BackupContentContract

    if manifest.object_contract is None:
        raise ValueError("Deployment requires a verified object contract; refresh the full backup")
    contract = BackupContentContract.model_validate(manifest.object_contract)
    if not contract.verified or contract.issues:
        raise ValueError("Deployment requires a verified object contract")
    from mercury.backup.checksum import safe_artifact_name

    if not safe_artifact_name(manifest.dump_file):
        raise ValueError("Unsafe manifest dump filename")
    _direct_path(directory / manifest.dump_file)
    raw_checksums, checksum_digest = _read_regular(directory / "checksum.sha256", limit=1024 * 1024)
    # Parse the bytes just hashed, never reopen mutable source metadata.
    with tempfile.TemporaryDirectory(prefix="mercury-deploy-metadata-") as temporary:
        checksum_copy = Path(temporary) / "checksum.sha256"
        checksum_copy.write_bytes(raw_checksums)
        entries = parse_checksum_file(checksum_copy)
    expected = entries.get(manifest.dump_file)
    if expected is None or (manifest.sha256 and manifest.sha256.lower() != expected):
        raise ValueError("Manifest and checksum do not identify the same dump")
    # Development authority is checked by the caller and by snapshot verification.
    return {"manifest_sha256": manifest_digest, "checksum_sha256": checksum_digest,
            "dump_sha256": expected}


@contextmanager
def bound_deployment_artifacts(candidate: DeploymentCandidate, *, backup_root: Path, allow_development_backup: bool = False):
    """Never hand the importer a mutable operator-supplied pathname.

    The private directory is retained through post-import verification. Changes
    to the source after capture cannot change either the import or its contract.
    Copy/validation failures occur before any database DDL.
    """
    directory = _direct_path(Path(candidate.backup_directory))
    if not directory.is_relative_to(_direct_path(backup_root)):
        raise ValueError("Selected backup is outside the configured backup root")
    for value in (candidate.manifest_sha256, candidate.checksum_sha256, candidate.dump_sha256):
        if value is None or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Deployment requires exact selected artifact hashes; rebuild the plan")
    if _direct_path(Path(candidate.manifest_path)) != directory / "manifest.json":
        raise ValueError("Selected manifest is outside the verified backup directory")
    if _direct_path(Path(candidate.checksum_path)) != directory / "checksum.sha256":
        raise ValueError("Selected checksum is outside the verified backup directory")

    with tempfile.TemporaryDirectory(prefix="mercury-deploy-inputs-") as temporary:
        snapshot = Path(temporary)
        for name, expected, limit in (
            ("manifest.json", candidate.manifest_sha256, 8 * 1024 * 1024),
            ("checksum.sha256", candidate.checksum_sha256, 1024 * 1024),
        ):
            _data, digest = _read_regular(directory / name, destination=snapshot / name, limit=limit)
            if digest != expected:
                raise ValueError(f"Selected {name} changed since selection")
        manifest = BackupManifest.model_validate(json.loads((snapshot / "manifest.json").read_bytes()))
        if manifest.database != candidate.source_database or manifest.backup_id != candidate.backup_id:
            raise ValueError("Selected backup ID/database does not match the pinned manifest")
        if manifest.backup_kind != "full" or manifest.object_scope != "complete_schema":
            raise ValueError("Deployment requires a complete full backup")
        entries = parse_checksum_file(snapshot / "checksum.sha256")
        if entries.get(manifest.dump_file) != candidate.dump_sha256:
            raise ValueError("Selected dump digest disagrees with the checksum contract")
        if _direct_path(Path(candidate.dump_path)) != directory / manifest.dump_file:
            raise ValueError("Selected dump path does not match the pinned manifest")
        for name, expected in entries.items():
            if name in {"manifest.json", "checksum.sha256"}:
                raise ValueError("Checksum entries must name data artifacts")
            _data, digest = _read_regular(directory / name, destination=snapshot / name)
            if digest != expected:
                raise ValueError(f"Pinned artifact checksum mismatch: {name}")
        verification = verify_backup_artifacts(snapshot, database=candidate.source_database,
            backup_kind="full", allow_development_backup=allow_development_backup)
        if not verification.verified:
            raise ValueError("Pinned backup verification failed: " + "; ".join(verification.issues))
        from mercury.backup.content_contract import BackupContentContract

        if manifest.object_contract is None:
            raise ValueError("Deployment requires a verified object contract; refresh the full backup")
        contract = BackupContentContract.model_validate(manifest.object_contract)
        if not contract.verified or contract.issues:
            raise ValueError("Deployment requires a verified object contract")
        yield snapshot / manifest.dump_file, snapshot / "manifest.json"
