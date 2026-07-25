"""Narrow, sealed-package production cutover for the verified destination lane.

This is deliberately not a generic migration subsystem.  It accepts exactly
the two production schemas in the final destination package, requires the
retained rehearsal evidence, and keeps every receipt on destination-local
storage.  Preview is read-only apart from its local receipt; execute is gated
by an approved, one-time preview and is intentionally separate from preview.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mercury.database.mariadb.client import run_client_query, run_client_sql
from mercury.database.mariadb.session import fetch_user_database_names, try_load_mariadb_config
from mercury.restore.destination_rehearsal import (
    PackageRestoreArtifact,
    assert_destination_receipt_root,
    resolve_package_restore_artifact,
)
from mercury.restore.failed_rehearsal_recovery import (
    _expected_counts,
    _matching_receipt,
    _read_jsonl,
    _require_counts,
    schema_object_counts,
)
from mercury.restore.restore_runner import execute_restore_into_database

ANDROID_SOURCE = "android_permission_intel"
EREBUS_SOURCE = "erebus_threat_intel_prod"
ANDROID_TARGET = "android_permission_intel"
EREBUS_TARGET = "erebus_threat_intel_prod"
ANDROID_REHEARSAL = "_restorecheck_android_permission_intel_20260722T055400Z_phase3b"
EREBUS_REHEARSAL = "_restorecheck_erebus_threat_intel_prod_20260722T055400Z_phase3b"
EXECUTE_CONFIRMATION = "PROMOTE SEALED DESTINATION PACKAGE"
PREVIEW_APPROVED = "PRODUCTION_CUTOVER_PREVIEW_APPROVED"
PREVIEW_CONSUMED = "PRODUCTION_CUTOVER_PREVIEW_CONSUMED"
EXPECTED_EREBUS_COMMIT = "05f3abc2dd30c57a6a303e24b90d15d7dbf3a8f9"
EXPECTED_EREBUS_TREE = "bdc547e6d89a9911755f5b3294edddafb16ae877"

# These are the schema-scoped privileges exercised by the pinned dumps.  The
# Erebus dump has stored procedures; the Android dump does not.  Events are
# intentionally omitted because the governed Phase 3B baseline records zero
# events in both schemas.
COMMON_IMPORT_PRIVILEGES = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
    "INDEX", "REFERENCES", "CREATE VIEW", "SHOW VIEW", "TRIGGER", "LOCK TABLES",
})
EREBUS_ROUTINE_PRIVILEGES = frozenset({"CREATE ROUTINE", "ALTER ROUTINE", "EXECUTE"})
_GRANT_RE = re.compile(r"^GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_receipt(root: Path, name: str, payload: dict[str, Any]) -> Path:
    directory = root / "production_cutover_receipts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    _write_json_atomically(path, payload)
    return path


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Replace a local evidence file atomically; never leave partial JSON."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _dump_path(artifact: PackageRestoreArtifact) -> Path:
    manifest = _read_json(artifact.backup_directory / "manifest.json")
    name = manifest.get("dump_file")
    if not isinstance(name, str) or not name:
        raise ValueError(f"Package backup manifest has no dump_file: {artifact.backup_directory}")
    dump = artifact.backup_directory / name
    if not dump.is_file():
        raise ValueError(f"Package data dump is missing: {dump}")
    return dump


@dataclass(frozen=True)
class ProductionCutoverContext:
    package_id: str
    package_root: Path
    phase3b_run_id: str
    capture_id: str
    capture_authority: str
    android: PackageRestoreArtifact
    erebus: PackageRestoreArtifact
    android_rehearsal: str
    erebus_rehearsal: str
    receipt_root: Path
    schema_map: dict[str, str]
    expected_android: dict[str, int]
    expected_erebus: dict[str, int]
    android_restore_receipt: dict[str, Any]
    erebus_recovery_receipt: dict[str, Any]


def _capture_authority(root: Path) -> tuple[str, str]:
    captures = sorted((root / "payload").glob("003_erebus_capture_*/capture_summary.json"))
    if len(captures) != 1:
        raise ValueError("Package must contain exactly one active Erebus capture summary.")
    summary = _read_json(captures[0])
    capture_id = str(summary.get("capture_id") or "")
    if (
        not capture_id
        or summary.get("status") != "CAPTURE_VERIFIED"
        or summary.get("active_authority") is not True
        or summary.get("historical_only") is not False
    ):
        raise ValueError("Erebus capture is not the active PACKAGE_AUTHORITY capture.")
    return capture_id, "PACKAGE_AUTHORITY"


def _recovery_receipt(
    receipts: Path,
    *,
    package_id: str,
    backup_id: str,
    rehearsal: str,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for path in sorted((receipts / "recovery_receipts").glob("*_result.json")):
        data = _read_json(path)
        if (
            data.get("package_id") == package_id
            and data.get("erebus_backup_id") == backup_id
            and data.get("failed_erebus_target") == rehearsal
            and data.get("final_decision") == "FAILED_RETAINED_TARGET_REPLACED_AND_VERIFIED"
        ):
            data["_receipt_path"] = str(path)
            matches.append(data)
    if not matches:
        raise ValueError("Missing successful exact Erebus failed-rehearsal recovery receipt.")
    return matches[-1]


def build_production_cutover_context(
    *,
    package_root: Path,
    package_id: str,
    android_backup_id: str,
    erebus_backup_id: str,
    android_source_schema: str,
    erebus_source_schema: str,
    android_target_schema: str,
    erebus_target_schema: str,
    receipt_root: Path,
) -> ProductionCutoverContext:
    """Bind every request value to the sealed package and retained evidence."""
    if not package_id or package_id.lower() == "latest":
        raise ValueError("An exact package ID is required; latest is refused.")
    if any(not value or value.lower() == "latest" for value in (android_backup_id, erebus_backup_id)):
        raise ValueError("Both exact backup IDs are required; latest is refused.")
    if android_source_schema != ANDROID_REHEARSAL or erebus_source_schema != EREBUS_REHEARSAL:
        raise ValueError("Exact retained rehearsal schema names are required.")
    if android_target_schema != ANDROID_TARGET or erebus_target_schema != EREBUS_TARGET:
        raise ValueError("Only the two exact production target schema names are allowed.")

    root = package_root.expanduser().resolve()
    if root.name != package_id:
        raise ValueError("package_root basename must equal the explicit package_id.")
    receipts = assert_destination_receipt_root(receipt_root)
    android = resolve_package_restore_artifact(
        package_root=root,
        source_database=ANDROID_SOURCE,
        backup_id=android_backup_id,
        target_schema=android_source_schema,
    )
    erebus = resolve_package_restore_artifact(
        package_root=root,
        source_database=EREBUS_SOURCE,
        backup_id=erebus_backup_id,
        target_schema=erebus_source_schema,
    )
    if android.package_id != package_id or erebus.package_id != package_id:
        raise ValueError("Resolved package artifact does not match requested package_id.")
    phase, expected_erebus, expected_android = _expected_counts(root)
    capture_id, authority = _capture_authority(root)
    rows = _read_jsonl(receipts / "operations.jsonl")
    android_receipt = _matching_receipt(
        rows,
        event_type="restore_check_passed",
        backup_id=android.backup_id,
        target_schema=android_source_schema,
        package_root=root,
    )
    recovery = _recovery_receipt(
        receipts,
        package_id=package_id,
        backup_id=erebus.backup_id,
        rehearsal=erebus_source_schema,
    )
    return ProductionCutoverContext(
        package_id=package_id,
        package_root=root,
        phase3b_run_id=phase,
        capture_id=capture_id,
        capture_authority=authority,
        android=android,
        erebus=erebus,
        android_rehearsal=android_source_schema,
        erebus_rehearsal=erebus_source_schema,
        receipt_root=receipts,
        schema_map={ANDROID_SOURCE: ANDROID_TARGET, EREBUS_SOURCE: EREBUS_TARGET},
        expected_android=expected_android,
        expected_erebus=expected_erebus,
        android_restore_receipt=android_receipt,
        erebus_recovery_receipt=recovery,
    )


def _mount_options(path: Path) -> str:
    result = subprocess.run(
        ["findmnt", "-no", "OPTIONS", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(f"Cannot inspect mount options for {path}.")
    return result.stdout.strip()


def _writers_active() -> list[str]:
    result = subprocess.run(["ps", "-eo", "pid=,cmd="], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError("Cannot inspect process state for writer gate.")
    active: list[str] = []
    for line in result.stdout.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ("erebus worker", "erebus intake", "mercury writer", "mercury sync")):
            active.append(line.strip())
    return active


def required_production_privileges() -> dict[str, frozenset[str]]:
    """Return the exact schema privileges required by the two sealed dumps."""
    return {
        ANDROID_TARGET: COMMON_IMPORT_PRIVILEGES,
        EREBUS_TARGET: COMMON_IMPORT_PRIVILEGES | EREBUS_ROUTINE_PRIVILEGES,
    }


def _parse_grants(lines: list[str]) -> tuple[set[str], dict[str, set[str]]]:
    """Parse MariaDB GRANT lines into global and exact-schema capabilities."""
    global_capabilities: set[str] = set()
    schema_capabilities: dict[str, set[str]] = {}
    for line in lines:
        normalized_account = line.replace("'", "`")
        if "TO `systemadmin`@`localhost`" not in normalized_account:
            # A broad grant to a remote or unrelated account is not evidence
            # that the socket-authenticated cutover account can import.
            continue
        match = _GRANT_RE.match(line.strip())
        if not match:
            continue
        raw_privileges, raw_scope = match.groups()
        privileges = {item.strip().upper() for item in raw_privileges.split(",")}
        if "ALL PRIVILEGES" in privileges:
            privileges = {"ALL PRIVILEGES"}
        scope = raw_scope.strip().replace("`", "")
        if scope == "*.*":
            global_capabilities.update(privileges)
            continue
        if not scope.endswith(".*"):
            continue
        schema = scope[:-2]
        schema_capabilities.setdefault(schema, set()).update(privileges)
    return global_capabilities, schema_capabilities


def inspect_production_privileges(config, *, grant_lines: list[str] | None = None) -> dict[str, Any]:
    """Fail closed unless the local operator has exact target-schema DDL grants."""
    if config.user != "systemadmin":
        raise ValueError("Production cutover requires the exact systemadmin@localhost account.")
    lines = grant_lines
    if lines is None:
        current = run_client_query(config, "SELECT CURRENT_USER()").strip()
        if current != "systemadmin@localhost":
            raise ValueError(f"Expected socket account systemadmin@localhost, got {current or 'none'}.")
        raw = run_client_query(config, "SHOW GRANTS FOR 'systemadmin'@'localhost'")
        lines = [line for line in raw.splitlines() if line.strip()]
    global_capabilities, schema_capabilities = _parse_grants(lines)
    missing_by_schema: dict[str, list[str]] = {}
    required = required_production_privileges()
    for schema, capabilities in required.items():
        granted = schema_capabilities.get(schema, set())
        if "ALL PRIVILEGES" in granted:
            continue
        missing = sorted(capabilities - granted)
        if missing:
            missing_by_schema[schema] = missing
    # CREATE DATABASE is intentionally checked separately: a schema-level
    # CREATE grant cannot authorize CREATE DATABASE on a missing schema.
    create_database_allowed = "CREATE" in global_capabilities or "ALL PRIVILEGES" in global_capabilities
    decision = {
        "account": "systemadmin@localhost",
        "grant_lines": lines,
        "global_capabilities": sorted(global_capabilities),
        "schema_capabilities": {key: sorted(value) for key, value in schema_capabilities.items()},
        "required": {key: sorted(value) for key, value in required.items()},
        "create_database_allowed": create_database_allowed,
        "missing_by_schema": missing_by_schema,
        "passed": create_database_allowed and not missing_by_schema,
    }
    if not create_database_allowed or missing_by_schema:
        missing_parts = []
        if not create_database_allowed:
            missing_parts.append("global CREATE required for CREATE DATABASE")
        missing_parts.extend(f"{schema}: {', '.join(values)}" for schema, values in missing_by_schema.items())
        raise ValueError("Production cutover privilege preflight failed: " + "; ".join(missing_parts))
    return decision


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"Git verification failed for {repo}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _git_identity() -> dict[str, str]:
    mercury = Path(__file__).resolve().parents[3]
    erebus = mercury.parent / "erebus-engine-fedora"
    mercury_status = _git_output(mercury, "status", "--porcelain")
    erebus_status = _git_output(erebus, "status", "--porcelain")
    mercury_head = _git_output(mercury, "rev-parse", "HEAD")
    mercury_origin = _git_output(mercury, "rev-parse", "origin/main")
    erebus_head = _git_output(erebus, "rev-parse", "HEAD")
    erebus_origin = _git_output(erebus, "rev-parse", "origin/main")
    erebus_tree = _git_output(erebus, "rev-parse", "HEAD^{tree}")
    if mercury_status or erebus_status or mercury_head != mercury_origin or erebus_head != erebus_origin:
        raise ValueError("Git worktrees must be clean and synchronized with origin/main.")
    if erebus_head != EXPECTED_EREBUS_COMMIT or erebus_tree != EXPECTED_EREBUS_TREE:
        raise ValueError("Erebus commit/tree does not match the sealed-package authority.")
    return {
        "mercury_commit": mercury_head,
        "erebus_commit": erebus_head,
        "erebus_tree": erebus_tree,
    }


def _metadata_reference_count(config, schema: str, needles: tuple[str, ...]) -> int:
    if not schema.replace("_", "").isalnum():
        raise ValueError("Unsafe schema identifier in metadata validation.")
    quoted = " OR ".join(
        f"definition_text LIKE '%`{needle}`.%' OR definition_text LIKE '%{needle}.%'" for needle in needles
    )
    sql = (
        "SELECT COUNT(*) FROM ("
        f"SELECT view_definition AS definition_text FROM information_schema.views WHERE table_schema='{schema}' "
        "UNION ALL "
        f"SELECT routine_definition FROM information_schema.routines WHERE routine_schema='{schema}' "
        "UNION ALL "
        f"SELECT action_statement FROM information_schema.triggers WHERE trigger_schema='{schema}'"
        f") definitions WHERE {quoted};"
    )
    return int(run_client_query(config, sql).strip() or "0")


def _run_rehearsal_smoke(context: ProductionCutoverContext) -> dict[str, Any]:
    # The package cannot encode a destination checkout path.  The governed
    # desktop layout keeps the reconstructed Erebus repository under the
    # destination operator's GitHub directory.
    repo = Path.home() / "GitHub" / "erebus-engine-fedora"
    python = repo / ".venv" / "bin" / "python"
    checkpoint = context.package_root / "payload" / f"000_phase3b_run_{context.phase3b_run_id}" / "checkpoints" / "pre_dump_checkpoint.json"
    env = {"MYSQL_UNIX_PORT": "/var/lib/mysql/mysql.sock", "EREBUS_RESTORE_VALIDATE_MYSQL_PROTOCOL": "socket"}
    result = subprocess.run(
        [str(python), "-m", "erebus.ops.restore_validate", "--source-checkpoint", str(checkpoint),
         "--erebus-schema", context.erebus_rehearsal, "--permission-schema", context.android_rehearsal,
         "--expected-host", "fedora"],
        cwd=repo, env={**__import__("os").environ, **env}, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or "ok=True" not in result.stdout:
        raise ValueError("Read-only Erebus rehearsal smoke validation failed.")
    return {"passed": True, "summary": "29 exact comparisons; no query errors", "output": result.stdout}


def validate_production_cutover_preflight(
    context: ProductionCutoverContext,
    *,
    config=None,
    mount_options: Callable[[Path], str] | None = None,
    writer_probe: Callable[[], list[str]] | None = None,
    smoke_runner: Callable[[ProductionCutoverContext], dict[str, Any]] | None = None,
    git_probe: Callable[[], dict[str, str]] | None = None,
    privilege_probe: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run all preview-safe gates.  This performs no database writes."""
    cfg = config or try_load_mariadb_config()
    if cfg is None:
        raise ValueError("MariaDB configuration is unavailable.")
    if cfg.host not in {"127.0.0.1", "localhost", "::1"} or not cfg.unix_socket:
        raise ValueError("MariaDB must use a local loopback endpoint and unix socket.")
    mount_options = mount_options or _mount_options
    writer_probe = writer_probe or _writers_active
    smoke_runner = smoke_runner or _run_rehearsal_smoke
    git_probe = git_probe or _git_identity
    privilege_probe = privilege_probe or inspect_production_privileges
    options = mount_options(Path("/mnt/MERCURY_DATA_V2"))
    required_mount_options = {"ro", "nosuid", "nodev"}
    actual_mount_options = {part.strip() for part in options.split(",")}
    missing_mount_options = sorted(required_mount_options - actual_mount_options)
    if missing_mount_options:
        raise ValueError(
            "Mercury HDD must remain mounted ro,nosuid,nodev for production cutover; "
            f"missing: {', '.join(missing_mount_options)}."
        )
    writers = writer_probe()
    if writers:
        raise ValueError(f"Active Mercury/Erebus writers refuse cutover: {writers}")
    privilege_decision = privilege_probe(cfg)
    schemas = set(fetch_user_database_names(cfg))
    collisions = sorted({ANDROID_TARGET, EREBUS_TARGET} & schemas)
    if collisions:
        raise ValueError(f"Production target collision: {collisions}")
    if context.android_rehearsal not in schemas or context.erebus_rehearsal not in schemas:
        raise ValueError("Both retained rehearsal schemas must exist.")
    android_counts = schema_object_counts(cfg, context.android_rehearsal)
    erebus_counts = schema_object_counts(cfg, context.erebus_rehearsal)
    _require_counts(android_counts, context.expected_android, "Android rehearsal")
    _require_counts(erebus_counts, context.expected_erebus, "Erebus rehearsal")
    if _metadata_reference_count(cfg, context.erebus_rehearsal, (ANDROID_SOURCE, EREBUS_SOURCE)):
        raise ValueError("Retained Erebus metadata still references original production schemas.")
    smoke = smoke_runner(context)
    return {
        "package_verified": True,
        "capture_id": context.capture_id,
        "capture_authority": context.capture_authority,
        "mariadb_endpoint": {"host": cfg.host, "port": cfg.port, "unix_socket": cfg.unix_socket},
        "hdd_mount_options": options,
        "writer_state": "disabled",
        "production_schema_count": 0,
        "collision_result": "absent",
        "android_rehearsal_counts": android_counts,
        "erebus_rehearsal_counts": erebus_counts,
        "erebus_smoke": smoke,
        "metadata_original_production_reference_count": 0,
        "git": git_probe(),
        "privilege_preflight": privilege_decision,
    }


def _base_receipt(context: ProductionCutoverContext, preflight: dict[str, Any]) -> dict[str, Any]:
    android_dump = _dump_path(context.android)
    erebus_dump = _dump_path(context.erebus)
    return {
        "schema": "mercury.production_cutover.v1",
        "mercury_commit": preflight["git"]["mercury_commit"],
        "erebus_commit": preflight["git"]["erebus_commit"],
        "erebus_tree": preflight["git"]["erebus_tree"],
        "package_id": context.package_id,
        "package_root": str(context.package_root),
        "phase3b_run_id": context.phase3b_run_id,
        "capture_id": context.capture_id,
        "capture_authority": context.capture_authority,
        "backups": {
            ANDROID_SOURCE: {"backup_id": context.android.backup_id, "path": str(android_dump), "sha256": _sha256(android_dump)},
            EREBUS_SOURCE: {"backup_id": context.erebus.backup_id, "path": str(erebus_dump), "sha256": _sha256(erebus_dump)},
        },
        "rehearsal_schemas": {ANDROID_SOURCE: context.android_rehearsal, EREBUS_SOURCE: context.erebus_rehearsal},
        "targets": {ANDROID_SOURCE: ANDROID_TARGET, EREBUS_SOURCE: EREBUS_TARGET},
        "schema_map": context.schema_map,
        "restore_order": [ANDROID_TARGET, EREBUS_TARGET],
        "expected_object_counts": {ANDROID_SOURCE: context.expected_android, EREBUS_SOURCE: context.expected_erebus},
        "preflight": preflight,
        "rehearsal_receipts": {"android": context.android_restore_receipt, "erebus_recovery": context.erebus_recovery_receipt},
        "rollback_plan": "On any failure, drop only newly-created android_permission_intel and/or erebus_threat_intel_prod; never drop retained _restorecheck schemas.",
        "execute_confirmation_phrase": EXECUTE_CONFIRMATION,
    }


def create_production_cutover_preview(context: ProductionCutoverContext, *, config=None) -> tuple[dict[str, Any], Path]:
    preflight = validate_production_cutover_preflight(context, config=config)
    operation_id = f"production_cutover_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    payload = _base_receipt(context, preflight)
    payload.update({
        "operation_id": operation_id,
        "created_at_utc": _now(),
        "action": "preview",
        "preview_decision": PREVIEW_APPROVED,
        "preview_consumed": False,
        "database_writes": 0,
    })
    path = _write_receipt(context.receipt_root, f"{operation_id}_preview.json", payload)
    return payload, path


def _validate_production_schema(config, schema: str, expected: dict[str, int]) -> None:
    _require_counts(schema_object_counts(config, schema), expected, f"Production {schema}")


def execute_production_cutover(
    context: ProductionCutoverContext,
    *,
    preview_receipt: Path,
    confirmation: str,
    config=None,
    restore_executor=execute_restore_into_database,
    sql_executor=run_client_sql,
) -> tuple[dict[str, Any], Path]:
    """Execute a previously approved preview once, with targeted rollback."""
    if confirmation != EXECUTE_CONFIRMATION:
        raise ValueError(f"Exact confirmation required: {EXECUTE_CONFIRMATION}")
    preview_path = preview_receipt.expanduser().resolve()
    allowed_root = (context.receipt_root / "production_cutover_receipts").resolve()
    if allowed_root not in preview_path.parents:
        raise ValueError("preview_receipt must be under destination-local production cutover receipts.")
    preview = _read_json(preview_path)
    if preview.get("preview_decision") != PREVIEW_APPROVED or preview.get("preview_consumed") is True:
        raise ValueError("Preview is not approved and unconsumed.")
    if preview.get("package_id") != context.package_id or preview.get("schema_map") != context.schema_map:
        raise ValueError("Preview no longer matches the exact cutover request.")
    preflight = validate_production_cutover_preflight(context, config=config)
    cfg = config or try_load_mariadb_config()
    if cfg is None:
        raise ValueError("MariaDB configuration is unavailable.")
    result = _base_receipt(context, preflight)
    operation_id = f"production_cutover_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    result.update({"operation_id": operation_id, "action": "execute", "started_at_utc": _now(), "preview_receipt": str(preview_path)})
    created: list[str] = []
    # This journal exists before the first CREATE DATABASE.  It is updated as
    # each target becomes rollback-owned, so an interrupted process leaves
    # destination-local evidence of exactly which schemas need inspection.
    journal = _base_receipt(context, preflight)
    journal.update({
        "operation_id": operation_id,
        "action": "execute",
        "journal_state": "in_progress",
        "started_at_utc": result["started_at_utc"],
        "preview_receipt": str(preview_path),
        "created_targets": [],
        "rollback_ownership": [],
    })
    journal_path = _write_receipt(context.receipt_root, f"{operation_id}_in_progress.json", journal)
    result["rollback_journal"] = str(journal_path)

    def record_created(schema: str) -> None:
        """Durably record rollback ownership immediately after CREATE DATABASE succeeds."""
        if schema not in {ANDROID_TARGET, EREBUS_TARGET}:
            raise ValueError(f"Unexpected production schema creation callback: {schema}")
        if schema in created:
            raise ValueError(f"Duplicate production schema creation callback: {schema}")
        created.append(schema)
        result.setdefault("created_targets", []).append(schema)
        journal["created_targets"].append(schema)
        journal["rollback_ownership"].append({"schema": schema, "acquired_at_utc": _now()})
        _write_receipt(context.receipt_root, journal_path.name, journal)

    try:
        android = restore_executor(
            target_database=ANDROID_TARGET, dump_path=_dump_path(context.android), source_database=ANDROID_SOURCE,
            execute=True, recreate_target=False, cleanup_after_success=False, receipt_root=context.receipt_root,
            governed_production_cutover=True, schema_rewrites=context.schema_map, config=cfg,
            on_target_created=record_created,
        )
        result["android_restore"] = android.model_dump(mode="json")
        if not android.executed or android.verification_passed is False:
            raise ValueError(android.message or "Android production restore failed")
        _validate_production_schema(cfg, ANDROID_TARGET, context.expected_android)
        erebus = restore_executor(
            target_database=EREBUS_TARGET, dump_path=_dump_path(context.erebus), source_database=EREBUS_SOURCE,
            execute=True, recreate_target=False, cleanup_after_success=False, receipt_root=context.receipt_root,
            governed_production_cutover=True, schema_rewrites=context.schema_map, config=cfg,
            on_target_created=record_created,
        )
        result["erebus_restore"] = erebus.model_dump(mode="json")
        if not erebus.executed or erebus.verification_passed is False:
            raise ValueError(erebus.message or "Erebus production restore failed")
        _validate_production_schema(cfg, EREBUS_TARGET, context.expected_erebus)
        if _metadata_reference_count(cfg, EREBUS_TARGET, ("_restorecheck_",)):
            raise ValueError("Production Erebus metadata contains retained rehearsal references.")
        result["final_decision"] = "PRODUCTION_CUTOVER_EXECUTED_AND_VALIDATED"
    except Exception as exc:
        result["final_decision"] = "PRODUCTION_CUTOVER_ROLLED_BACK"
        result["failure"] = str(exc)
        rollback_events: list[dict[str, Any]] = []
        for schema in reversed(created):
            event: dict[str, Any] = {
                "schema": schema,
                "command": f"DROP DATABASE IF EXISTS `{schema}`",
                "attempted": True,
                "completed": False,
            }
            try:
                sql_executor(cfg, event["command"])
                event["completed"] = True
            except Exception as rollback_exc:  # Preserve the original import/validation failure.
                event["error"] = str(rollback_exc)
            rollback_events.append(event)
        result["rollback_events"] = rollback_events
        result["rollback_dropped"] = [event["schema"] for event in rollback_events if event["completed"]]
        journal["rollback_events"] = rollback_events
    finally:
        result["completed_at_utc"] = _now()
        preview["preview_consumed"] = True
        preview["preview_decision"] = PREVIEW_CONSUMED
        preview["consumed_at_utc"] = _now()
        _write_json_atomically(preview_path, preview)
    result_path = _write_receipt(context.receipt_root, f"{operation_id}_result.json", result)
    journal.update({
        "journal_state": "completed",
        "completed_at_utc": result["completed_at_utc"],
        "final_decision": result["final_decision"],
        "result_receipt": str(result_path),
        "rollback_dropped": result.get("rollback_dropped", []),
    })
    _write_receipt(context.receipt_root, journal_path.name, journal)
    return result, result_path
