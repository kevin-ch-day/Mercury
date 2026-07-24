"""Package-bound recovery of one failed retained destination rehearsal target."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mercury.database.mariadb.client import run_client_query
from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config
from mercury.restore.destination_rehearsal import (
    PackageRestoreArtifact,
    assert_destination_receipt_root,
    resolve_package_restore_artifact,
)
from mercury.restore.restore_runner import execute_restore_into_database


@dataclass(frozen=True)
class FailedRehearsalRecovery:
    package_id: str
    package_root: Path
    phase3b_run_id: str
    erebus: PackageRestoreArtifact
    android: PackageRestoreArtifact
    schema_map: dict[str, str]
    receipt_root: Path
    failed_receipt: dict[str, Any]
    android_receipt: dict[str, Any]
    expected_erebus: dict[str, int]
    expected_android: dict[str, int]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _matching_receipt(
    rows: list[dict[str, Any]],
    *,
    event_type: str,
    backup_id: str,
    target_schema: str,
    package_root: Path,
) -> dict[str, Any]:
    root = str(package_root.resolve()) + "/"
    matches = [
        row
        for row in rows
        if row.get("event_type") == event_type
        and row.get("backup_id") == backup_id
        and row.get("target_database") == target_schema
        and str(row.get("backup_directory_path") or "").startswith(root)
    ]
    if not matches:
        raise ValueError(
            f"Missing exact {event_type} receipt for backup={backup_id} target={target_schema}."
        )
    return matches[-1]


def _expected_counts(package_root: Path) -> tuple[str, dict[str, int], dict[str, int]]:
    paths = sorted((package_root / "payload").glob("000_phase3b_run_*/restore/source_vs_restore_comparison.json"))
    if len(paths) != 1:
        raise ValueError("Package must contain exactly one Phase 3B restore comparison.")
    phase = paths[0].parents[1].name.removeprefix("000_phase3b_run_")
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    checkpoints = data.get("restore_checkpoints") or {}

    def counts(name: str) -> dict[str, int]:
        raw = checkpoints.get(name)
        if not isinstance(raw, dict):
            raise ValueError(f"Phase 3B comparison lacks {name} restore checkpoint.")
        result: dict[str, int] = {}
        for key in ("table_count", "view_count", "trigger_count", "routine_count", "event_count"):
            value = raw.get(key)
            if not isinstance(value, int):
                raise ValueError(f"Phase 3B comparison lacks integer {name}.{key}.")
            result[key] = value
        return result

    return phase, counts("erebus"), counts("android")


def schema_object_counts(config, schema: str) -> dict[str, int]:
    """Read-only object counts for one validated schema identifier."""
    if not schema.replace("_", "").isalnum():
        raise ValueError("Invalid schema identifier for recovery inspection.")
    sql = (
        "SELECT "
        "(SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='" + schema + "' AND table_type='BASE TABLE'),"
        "(SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='" + schema + "' AND table_type='VIEW'),"
        "(SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema='" + schema + "'),"
        "(SELECT COUNT(*) FROM information_schema.routines WHERE routine_schema='" + schema + "'),"
        "(SELECT COUNT(*) FROM information_schema.events WHERE event_schema='" + schema + "');"
    )
    raw = run_client_query(config, sql).strip()
    values = raw.split("\t") if raw else []
    if len(values) != 5:
        raise ValueError(f"Could not inspect schema object counts for {schema}.")
    return dict(zip(("table_count", "view_count", "trigger_count", "routine_count", "event_count"), map(int, values), strict=True))


def _require_counts(actual: dict[str, int], expected: dict[str, int], label: str) -> None:
    mismatches = {key: (actual.get(key), expected[key]) for key in expected if actual.get(key) != expected[key]}
    if mismatches:
        raise ValueError(f"{label} object counts do not match Phase 3B: {mismatches}")


def build_failed_rehearsal_recovery(
    *,
    package_root: Path,
    erebus_backup_id: str,
    erebus_target: str,
    android_backup_id: str,
    android_target: str,
    receipt_root: Path,
) -> FailedRehearsalRecovery:
    """Bind recovery inputs to sealed package and exact local receipts."""
    root = package_root.expanduser().resolve()
    receipts = assert_destination_receipt_root(receipt_root)
    erebus = resolve_package_restore_artifact(
        package_root=root,
        source_database="erebus_threat_intel_prod",
        backup_id=erebus_backup_id,
        target_schema=erebus_target,
    )
    android = resolve_package_restore_artifact(
        package_root=root,
        source_database="android_permission_intel",
        backup_id=android_backup_id,
        target_schema=android_target,
    )
    phase, expected_erebus, expected_android = _expected_counts(root)
    rows = _read_jsonl(receipts / "operations.jsonl")
    failed = _matching_receipt(
        rows,
        event_type="restore_check_failed",
        backup_id=erebus.backup_id,
        target_schema=erebus.target_schema,
        package_root=root,
    )
    android_ok = _matching_receipt(
        rows,
        event_type="restore_check_passed",
        backup_id=android.backup_id,
        target_schema=android.target_schema,
        package_root=root,
    )
    return FailedRehearsalRecovery(
        package_id=erebus.package_id,
        package_root=root,
        phase3b_run_id=phase,
        erebus=erebus,
        android=android,
        schema_map={
            "erebus_threat_intel_prod": erebus.target_schema,
            "android_permission_intel": android.target_schema,
        },
        receipt_root=receipts,
        failed_receipt=failed,
        android_receipt=android_ok,
        expected_erebus=expected_erebus,
        expected_android=expected_android,
    )


def validate_failed_rehearsal_recovery(context: FailedRehearsalRecovery, *, config=None) -> dict[str, dict[str, int]]:
    """Fail closed before dropping the one failed disposable target."""
    cfg = config or try_load_mariadb_config()
    if cfg is None:
        raise ValueError("MariaDB configuration is unavailable for recovery validation.")
    schemas = set(fetch_user_database_names(cfg))
    forbidden = {"erebus_threat_intel_prod", "android_permission_intel"}
    if schemas & forbidden:
        raise ValueError(f"Production schemas must be absent: {sorted(schemas & forbidden)}")
    if context.erebus.target_schema not in schemas:
        raise ValueError("Failed Erebus target is absent; replacement is not authorized.")
    if context.android.target_schema not in schemas:
        raise ValueError("Successful Android target is absent; replacement is not authorized.")
    android_counts = schema_object_counts(cfg, context.android.target_schema)
    _require_counts(android_counts, context.expected_android, "Android retained target")
    failed_counts = schema_object_counts(cfg, context.erebus.target_schema)
    # A fully matching target may never be replaced by the failure recovery lane.
    if failed_counts == context.expected_erebus:
        raise ValueError("Erebus target already matches Phase 3B; replacement is not authorized.")
    return {"failed_erebus": failed_counts, "android": android_counts}


def _write_receipt(root: Path, name: str, payload: dict[str, Any]) -> Path:
    directory = root / "recovery_receipts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def execute_failed_rehearsal_recovery(context: FailedRehearsalRecovery, *, config=None) -> tuple[dict[str, Any], Path]:
    """Replace only the receipt-proven failed Erebus target, once."""
    cfg = config or try_load_mariadb_config()
    if cfg is None:
        raise ValueError("MariaDB configuration is unavailable for recovery execution.")
    pre_counts = validate_failed_rehearsal_recovery(context, config=cfg)
    operation_id = f"failed_rehearsal_recovery_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    base: dict[str, Any] = {
        "schema": "mercury.failed_rehearsal_recovery.v1",
        "operation_id": operation_id,
        "package_id": context.package_id,
        "package_root": str(context.package_root),
        "phase3b_run_id": context.phase3b_run_id,
        "erebus_backup_id": context.erebus.backup_id,
        "android_backup_id": context.android.backup_id,
        "failed_erebus_target": context.erebus.target_schema,
        "retained_android_target": context.android.target_schema,
        "schema_map": context.schema_map,
        "failed_erebus_receipt": context.failed_receipt,
        "successful_android_receipt": context.android_receipt,
        "pre_drop_object_counts": pre_counts["failed_erebus"],
        "android_object_counts_before": pre_counts["android"],
        "authorization_decision": "FAILED_RECEIPT_VALIDATED_FOR_EXACT_DISPOSABLE_TARGET",
        "authorized_at_utc": _now(),
    }
    authorization_path = _write_receipt(context.receipt_root, f"{operation_id}_authorization.json", base)
    dump_path = context.erebus.backup_directory / json.loads(
        (context.erebus.backup_directory / "manifest.json").read_text(encoding="utf-8")
    )["dump_file"]
    started_at = _now()
    result = execute_restore_into_database(
        target_database=context.erebus.target_schema,
        dump_path=dump_path,
        source_database=context.erebus.source_database,
        execute=True,
        recreate_target=True,
        cleanup_after_success=False,
        receipt_root=context.receipt_root,
        governed_destination_rehearsal=True,
        schema_rewrites=context.schema_map,
        config=cfg,
    )
    post_counts = schema_object_counts(cfg, context.erebus.target_schema) if result.executed else {}
    android_counts = schema_object_counts(cfg, context.android.target_schema)
    decision = "FAILED_RETAINED_TARGET_REPLACED_AND_VERIFIED"
    production_absent = False
    try:
        _require_counts(post_counts, context.expected_erebus, "Recovered Erebus target")
        _require_counts(android_counts, context.expected_android, "Android retained target")
        schemas = set(fetch_user_database_names(cfg))
        production_absent = not bool({"erebus_threat_intel_prod", "android_permission_intel"} & schemas)
        if not production_absent:
            raise ValueError("Production schemas appeared during recovery.")
        if result.verification_passed is False:
            raise ValueError(result.verification_detail or "Mercury post-restore verification failed")
    except ValueError as exc:
        decision = "FAILED_RETAINED_TARGET_REPLACEMENT_INCOMPLETE"
        base["validation_error"] = str(exc)
    base.update(
        {
            "authorization_receipt": str(authorization_path),
            "restore_command": result.commands,
            "drop_result": "dropped_and_recreated" if result.executed else "not_completed",
            "dump_sha256": _sha256(dump_path),
            "restore_started_at_utc": started_at,
            "restore_exit_code": 0 if result.executed and result.verification_passed is not False else 1,
            "restore_result": result.model_dump(mode="json"),
            "post_restore_object_counts": post_counts,
            "android_object_counts_after": android_counts,
            "source_vs_restore_comparison": "PASS" if decision == "FAILED_RETAINED_TARGET_REPLACED_AND_VERIFIED" else "FAIL",
            "production_schemas_absent": production_absent,
            "completed_at_utc": _now(),
            "final_decision": decision,
        }
    )
    final_path = _write_receipt(context.receipt_root, f"{operation_id}_result.json", base)
    return base, final_path
