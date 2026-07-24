"""Pinned, package-backed destination rehearsal restore helpers.

This module deliberately has no fallback to the operator backup history.  A
governed destination rehearsal names both an exact package and an exact backup
ID, and reads the immutable payload copied into that package.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mercury.backup.verification import verify_backup_artifacts
from mercury.core.safety import BACKUP_KIND_FULL


DESTINATION_RECEIPT_ROOT = Path.home() / ".local" / "share" / "mercury"
PRODUCTION_REHEARSAL_SOURCES = frozenset(
    {"erebus_threat_intel_prod", "android_permission_intel"}
)
_MYSQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class PackageRestoreArtifact:
    """One exact, verified backup selected from a sealed destination package."""

    package_id: str
    package_root: Path
    backup_id: str
    source_database: str
    backup_directory: Path
    target_schema: str


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    return []


def _package_target_schema(package_root: Path, source_database: str) -> str:
    checklist = package_root / "destination_documents" / "destination_acceptance_checklist.json"
    values = _strings(_json_object(checklist))
    prefix = f"_restorecheck_{source_database}_"
    targets = sorted(
        {
            value
            for value in values
            if value.startswith(prefix) and _MYSQL_IDENTIFIER_RE.fullmatch(value)
        }
    )
    if len(targets) != 1:
        raise ValueError(
            "Package acceptance checklist must define exactly one retained "
            f"restore-check schema for {source_database}; found {targets or 'none'}."
        )
    return targets[0]


def assert_destination_receipt_root(receipt_root: Path) -> Path:
    """Only allow receipts in destination-local Mercury state."""
    resolved = receipt_root.expanduser().resolve()
    allowed = DESTINATION_RECEIPT_ROOT.resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(
            f"receipt root must be under destination-local state: {allowed}"
        ) from exc
    return resolved


def resolve_package_restore_artifact(
    *,
    package_root: Path,
    source_database: str,
    backup_id: str,
    target_schema: str,
) -> PackageRestoreArtifact:
    """Validate and select an exact backup directly from a sealed package."""
    if source_database not in PRODUCTION_REHEARSAL_SOURCES:
        raise ValueError(f"{source_database!r} is not an approved destination rehearsal source.")
    if not backup_id or backup_id.strip().lower() == "latest":
        raise ValueError("An exact backup_id is required; latest is refused.")
    if target_schema in PRODUCTION_REHEARSAL_SOURCES:
        raise ValueError(f"Refusing production schema target: {target_schema}")

    root = package_root.expanduser().resolve()
    receipt = _json_object(root / "package_receipt.json")
    package_id = str(receipt.get("package_id") or "")
    status = str(receipt.get("verification_status") or "")
    if not package_id or status != "DESTINATION_PACKAGE_VERIFIED":
        raise ValueError("Package is not DESTINATION_PACKAGE_VERIFIED.")

    expected_target = _package_target_schema(root, source_database)
    if target_schema != expected_target:
        raise ValueError(
            f"Target schema must exactly match the package-defined retained schema: {expected_target}"
        )
    if not target_schema.startswith("_restorecheck_") or not _MYSQL_IDENTIFIER_RE.fullmatch(target_schema):
        raise ValueError("Target schema must use the _restorecheck_ naming pattern.")

    from mercury.storage.detach_wizard import verify_package_manifest

    errors = verify_package_manifest(root)
    if errors:
        raise ValueError("Package checksum verification failed: " + "; ".join(errors))

    matches: list[Path] = []
    for manifest_path in sorted((root / "payload").glob("*/manifest.json")):
        manifest = _json_object(manifest_path)
        if (
            str(manifest.get("backup_id") or "") == backup_id
            and str(manifest.get("database") or "") == source_database
        ):
            matches.append(manifest_path.parent)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one package payload for backup_id {backup_id!r}; found {len(matches)}."
        )

    artifact = matches[0]
    verified = verify_backup_artifacts(
        artifact,
        database=source_database,
        backup_kind=BACKUP_KIND_FULL,
    )
    if not verified.verified or verified.backup_id != backup_id:
        raise ValueError(f"Pinned backup verification failed for {backup_id}.")

    return PackageRestoreArtifact(
        package_id=package_id,
        package_root=root,
        backup_id=backup_id,
        source_database=source_database,
        backup_directory=artifact,
        target_schema=target_schema,
    )
