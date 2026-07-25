"""Narrow, package-pinned restoration of the five approved destination schemas."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

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


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_receipt(
    receipt_root: Path,
    plan: "DestinationRecoveryPlan",
    *,
    decision: str,
    result=None,
    inspection=None,
    error: str | None = None,
) -> Path:
    """Persist destination recovery evidence outside the sealed package.

    The generic restore-check ledger deliberately ignores non-temporary
    schemas.  This narrow lane restores approved destination schemas, so it
    must own its receipt rather than silently reporting none.
    """
    operation_id = f"destination_recovery_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    directory = receipt_root / "destination_recovery_receipts"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "operation_id": operation_id,
        "recorded_at_utc": _now(),
        "decision": decision,
        "package_id": plan.package_id,
        "package_root": str(plan.package_root),
        "backup_id": plan.backup_id,
        "source_schema": plan.source_schema,
        "target_schema": plan.target_schema,
        "backup_directory": str(plan.backup_directory),
        "dump_path": str(plan.dump_path),
        "collision_policy": "refuse existing target; never overwrite",
    }
    if result is not None:
        payload["restore_result"] = (
            result.model_dump(mode="json")
            if hasattr(result, "model_dump")
            else dict(vars(result))
        )
    if inspection is not None:
        payload["inspection"] = {
            "exists_on_server": inspection.exists_on_server,
            "connected": inspection.connected,
            "table_count": inspection.table_count,
            "view_count": inspection.view_count,
        }
    if error is not None:
        payload["error"] = error
    path = directory / f"{operation_id}_{decision.lower()}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


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
        receipt = _write_receipt(
            receipts,
            plan,
            decision="FAILED",
            result=result,
            error=result.message or "Destination recovery import or verification failed.",
        )
        raise ValueError(
            f"{result.message or 'Destination recovery import or verification failed.'} "
            f"Failure receipt: {receipt}"
        )
    inspect = inspect_database_on_server(plan.target_schema, config)
    if not inspect.exists_on_server or not inspect.connected or not inspect.table_count:
        receipt = _write_receipt(
            receipts,
            plan,
            decision="FAILED",
            result=result,
            inspection=inspect,
            error="Post-restore schema inspection did not confirm populated target.",
        )
        raise ValueError(f"Post-restore schema inspection did not confirm populated target. Failure receipt: {receipt}")
    result.receipt_path = str(
        _write_receipt(receipts, plan, decision="VERIFIED", result=result, inspection=inspect)
    )
    return result, inspect
