"""Narrow, package-pinned restoration of the five approved destination schemas."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from mercury.backup.verification import verify_backup_artifacts
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.session import fetch_user_database_names
from mercury.restore.destination_rehearsal import assert_destination_receipt_root
from mercury.restore.restore_runner import execute_restore_into_database

CONFIRMATION = "RESTORE DESTINATION DATABASE"
ALLOWED_SCHEMAS = frozenset(
    {
        "android_permission_intel_dev",
        "erebus_threat_intel_dev",
        "scytaledroid_core_prod",
        "scytaledroid_core_dev",
        "obsidiandroid_core_prod",
    }
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class DestinationRecoveryPlan:
    package_id: str
    package_root: Path
    source_schema: str
    target_schema: str
    backup_id: str
    backup_directory: Path
    dump_path: Path
    allowed: bool
    blockers: tuple[str, ...]


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def build_destination_recovery_plan(
    *,
    package_root: Path,
    package_id: str,
    source_schema: str,
    target_schema: str,
    backup_id: str,
    config: MariaDbConnectionConfig | None = None,
) -> DestinationRecoveryPlan:
    """Bind one absent target to one verified, immutable package payload."""
    if not package_id or package_id.lower() == "latest":
        raise ValueError("An exact package_id is required; latest is refused.")
    if not backup_id or backup_id.lower() == "latest":
        raise ValueError("An exact backup_id is required; latest is refused.")
    if source_schema not in ALLOWED_SCHEMAS or target_schema != source_schema:
        raise ValueError("Source and target must be the same approved missing destination schema.")
    if not _IDENTIFIER.fullmatch(target_schema):
        raise ValueError("Target schema is not a valid MariaDB identifier.")

    root = package_root.expanduser().resolve()
    if root.name != package_id:
        raise ValueError("package_root basename must equal the explicit package_id.")
    receipt = _read_object(root / "package_receipt.json")
    if receipt.get("package_id") != package_id or receipt.get("verification_status") != "DESTINATION_PACKAGE_VERIFIED":
        raise ValueError("Package is not the exact DESTINATION_PACKAGE_VERIFIED package.")

    from mercury.storage.detach_wizard import verify_package_manifest

    errors = verify_package_manifest(root)
    if errors:
        raise ValueError("Package checksum verification failed: " + "; ".join(errors))
    package_manifest = _read_object(root / "package_manifest.json")
    members = [
        member for member in package_manifest.get("members") or []
        if isinstance(member, dict) and member.get("kind") == "backup" and member.get("identity") == backup_id
    ]
    if len(members) != 1:
        raise ValueError(f"Expected exactly one package member for backup_id {backup_id!r}.")
    relative = str(members[0].get("package_relative") or "")
    artifact = (root / relative).resolve()
    if not artifact.is_relative_to(root) or not artifact.is_dir():
        raise ValueError("Package backup member path is invalid.")
    manifest = _read_object(artifact / "manifest.json")
    if manifest.get("database") != source_schema or manifest.get("backup_id") != backup_id:
        raise ValueError("Package backup manifest does not match the requested schema and backup ID.")
    verified = verify_backup_artifacts(
        artifact,
        database=source_schema,
        backup_kind=BACKUP_KIND_FULL,
        allow_development_backup=source_schema.endswith("_dev"),
    )
    if not verified.verified or verified.backup_id != backup_id:
        raise ValueError("Pinned package backup verification failed.")
    dump_name = str(manifest.get("dump_file") or "")
    dump_path = artifact / dump_name
    if not dump_name or not dump_path.is_file():
        raise ValueError("Pinned package data dump is missing.")

    blockers: list[str] = []
    if config is None:
        blockers.append("MariaDB configuration is unavailable; target collision cannot be checked.")
    else:
        try:
            if target_schema in set(fetch_user_database_names(config)):
                blockers.append(f"Target schema already exists: {target_schema}")
        except Exception as exc:
            blockers.append(f"Target schema cannot be checked safely: {exc}")
    return DestinationRecoveryPlan(
        package_id=package_id,
        package_root=root,
        source_schema=source_schema,
        target_schema=target_schema,
        backup_id=backup_id,
        backup_directory=artifact,
        dump_path=dump_path,
        allowed=not blockers,
        blockers=tuple(blockers),
    )


def execute_destination_recovery(
    plan: DestinationRecoveryPlan,
    *,
    config: MariaDbConnectionConfig,
    receipt_root: Path,
):
    """Create/import one approved absent schema and roll it back on failure."""
    if not plan.allowed:
        raise ValueError("Destination recovery plan is blocked: " + "; ".join(plan.blockers))
    receipts = assert_destination_receipt_root(receipt_root)
    result = execute_restore_into_database(
        target_database=plan.target_schema,
        source_database=plan.source_schema,
        dump_path=plan.dump_path,
        execute=True,
        recreate_target=False,
        cleanup_after_success=False,
        receipt_root=receipts,
        governed_destination_recovery=True,
        rollback_new_target_on_failure=True,
        config=config,
    )
    if not result.executed or result.verification_passed is not True:
        raise ValueError(result.message or "Destination recovery import or verification failed.")
    inspect = inspect_database_on_server(plan.target_schema, config)
    if not inspect.exists_on_server or not inspect.connected or not inspect.table_count:
        raise ValueError("Post-restore schema inspection did not confirm populated target.")
    return result, inspect
