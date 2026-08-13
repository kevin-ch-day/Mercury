"""Read-only, artifact-bound capability preflight for prod→dev resets."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from mercury.backup.checksum import sha256_file
from mercury.backup.content_contract import (
    IMPORT_TRANSFORM_VERSION,
    RESTORE_REQUIREMENTS_CONTRACT_VERSION,
    RESTORE_REQUIREMENTS_SCANNER_VERSION,
    RestoreRequirementsContract,
)
from mercury.database.mariadb.client import run_client_query
from mercury.database.mariadb.config import MariaDbConfigError, load_mariadb_restore_config

_GRANT_RE = re.compile(r"^GRANT\s+(.+?)\s+ON\s+(.+?)\s+TO\s+", re.IGNORECASE)
_STATEMENT_CAPABILITIES = {
    "DROP TABLE": {"DROP"}, "CREATE TABLE": {"CREATE"}, "ALTER TABLE": {"ALTER"},
    "CREATE INDEX": {"INDEX"}, "DROP INDEX": {"INDEX"}, "INSERT": {"INSERT"},
    "REPLACE": {"INSERT"}, "CREATE VIEW": {"CREATE VIEW"}, "DROP VIEW": {"DROP"},
    "CREATE TRIGGER": {"TRIGGER"}, "DROP TRIGGER": {"TRIGGER"},
    "CREATE PROCEDURE": {"CREATE ROUTINE"}, "DROP PROCEDURE": {"ALTER ROUTINE"},
    "ALTER PROCEDURE": {"ALTER ROUTINE"}, "CREATE FUNCTION": {"CREATE ROUTINE"},
    "DROP FUNCTION": {"ALTER ROUTINE"}, "ALTER FUNCTION": {"ALTER ROUTINE"},
    "CREATE EVENT": {"EVENT"}, "DROP EVENT": {"EVENT"}, "ALTER EVENT": {"EVENT"},
    "LOCK TABLES": {"LOCK TABLES"}, "UNLOCK TABLES": {"LOCK TABLES"},
    "CREATE DATABASE": {"CREATE"}, "DROP DATABASE": {"DROP"},
}
_EXECUTION_PLAN = ["DROP DATABASE", "CREATE DATABASE", "SELECT metadata"]


class RestorePrivilegePreflightResult(BaseModel):
    """Durable assertion that an exact dump can be restored into one dev target."""

    passed: bool = False
    source_database: str
    target_database: str
    backup_id: str = ""
    artifact_file: str = ""
    artifact_sha256: str = ""
    current_user: str | None = None
    authenticated_user: str | None = None
    active_roles: list[str] = Field(default_factory=list)
    server_version: str | None = None
    server_conditions: dict[str, str] = Field(default_factory=dict)
    artifact_statement_classes: list[str] = Field(default_factory=list)
    execution_plan_operations: list[str] = Field(default_factory=lambda: list(_EXECUTION_PLAN))
    required_capabilities: list[str] = Field(default_factory=list)
    effective_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    unknown_requirements: list[str] = Field(default_factory=list)
    inspection_issues: list[str] = Field(default_factory=list)
    target_untouched: bool = True
    receipt_path: str | None = None
    receipt_sha256: str | None = None
    evidence_written: bool = False


def _capabilities(grants: list[str], target: str) -> set[str]:
    result: set[str] = set()
    for line in grants:
        match = _GRANT_RE.match(line.strip())
        if not match:
            continue
        privileges, scope = match.groups()
        scope = scope.replace("`", "").strip()
        if scope not in {"*.*", f"{target}.*"}:
            continue
        result.update(part.strip().upper() for part in privileges.split(","))
    return result


def _query_scalar(query: Callable, config, sql: str) -> str:
    return str(query(config, sql)).strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _receipt_root() -> Path:
    from mercury.core.storage_roles import CONTROL_DIRNAME
    from mercury.core.usb_mount import resolve_operator_mount

    return resolve_operator_mount() / CONTROL_DIRNAME / "restore_preflights"


def write_restore_preflight_receipt(
    result: RestorePrivilegePreflightResult, *, receipt_root: Path | None = None
) -> RestorePrivilegePreflightResult:
    """Write private immutable evidence for both pass and refusal outcomes."""
    root = receipt_root or _receipt_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    identity = hashlib.sha256(
        f"{result.source_database}|{result.target_database}|{result.backup_id}|{result.artifact_sha256}".encode()
    ).hexdigest()[:16]
    path = root / f"{stamp}_{identity}.json"
    payload = result.model_dump(mode="json")
    payload.update({"timestamp": datetime.now(timezone.utc).isoformat(), "target_untouched": True})
    payload.pop("receipt_path", None)
    payload.pop("receipt_sha256", None)
    payload.pop("evidence_written", None)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    _atomic_write(path.with_suffix(".json.sha256"), f"{digest}  {path.name}\n")
    return result.model_copy(update={"receipt_path": str(path), "receipt_sha256": digest, "evidence_written": True})


def _contract_issues(manifest: dict, dump_path: Path, contract: RestoreRequirementsContract) -> list[str]:
    issues: list[str] = []
    if contract.contract_version != RESTORE_REQUIREMENTS_CONTRACT_VERSION:
        issues.append("unsupported restore-requirements contract version")
    if contract.import_transform_version != IMPORT_TRANSFORM_VERSION:
        issues.append("restore-requirements contract is incompatible with the active importer")
    if contract.scanner_version != RESTORE_REQUIREMENTS_SCANNER_VERSION:
        issues.append("restore-requirements contract was generated by an unsupported scanner")
    if not contract.verified or contract.issues:
        issues.append("restore-requirements contract is not verified")
    if contract.artifact_file != manifest.get("dump_file") or contract.artifact_file != dump_path.name:
        issues.append("restore-requirements artifact filename does not match selected dump")
    if contract.artifact_sha256 != manifest.get("sha256"):
        issues.append("restore-requirements artifact checksum does not match manifest")
    if not dump_path.is_file() or contract.artifact_sha256 != sha256_file(dump_path):
        issues.append("restore-requirements artifact checksum does not match selected dump")
    return issues


def evaluate_restore_privileges(
    *,
    source: str,
    target: str,
    backup_dir: Path,
    query_fn: Callable | None = None,
    config=None,
    receipt_root: Path | None = None,
    write_receipt: bool = True,
) -> RestorePrivilegePreflightResult:
    """Inspect the actual configured MariaDB identity without modifying MariaDB.

    The returned record is intentionally bound to the selected manifest/dump and
    becomes eligible for execution only after its private receipt is written.
    """
    query = query_fn or run_client_query
    result = RestorePrivilegePreflightResult(source_database=source, target_database=target)
    try:
        manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        dump_name = str(manifest.get("dump_file") or "")
        dump_path = backup_dir / dump_name
        contract = RestoreRequirementsContract.model_validate(manifest.get("restore_requirements"))
        result.backup_id = str(manifest.get("backup_id") or "")
        result.artifact_file = contract.artifact_file
        result.artifact_sha256 = contract.artifact_sha256
        result.artifact_statement_classes = sorted(contract.statement_classes)
        result.inspection_issues.extend(_contract_issues(manifest, dump_path, contract))
        result.unknown_requirements.extend(contract.unknown_privileged_statements)
    except Exception as exc:
        result.inspection_issues.append(
            "Selected backup predates Mercury's verified restore-requirements contract; "
            "development target will not be modified."
        )
        result.inspection_issues.append(f"contract inspection failed: {exc}")
        return _persist(result, receipt_root, write_receipt)

    try:
        cfg = config or load_mariadb_restore_config()
    except MariaDbConfigError as exc:
        result.inspection_issues.append(
            "Dedicated [mariadb_restore] credentials are unavailable; "
            f"development target will not be modified: {exc}"
        )
        return _persist(result, receipt_root, write_receipt)
    try:
        identity = _query_scalar(query, cfg, "SELECT VERSION(), CURRENT_USER(), USER(), COALESCE(CURRENT_ROLE(), 'NONE')").split("\t")
        if len(identity) != 4:
            raise ValueError("identity query returned an unexpected column count")
        result.server_version, result.current_user, result.authenticated_user, role = identity
        if role not in {"", "NONE"}:
            result.active_roles = [part.strip() for part in role.split(",") if part.strip()]
        vars_row = _query_scalar(query, cfg, "SELECT @@automatic_sp_privileges, @@log_bin, @@log_bin_trust_function_creators").split("\t")
        if len(vars_row) != 3:
            raise ValueError("server-condition query returned an unexpected column count")
        result.server_conditions = dict(zip(("automatic_sp_privileges", "log_bin", "log_bin_trust_function_creators"), vars_row, strict=True))
        grants = [line for line in _query_scalar(query, cfg, "SHOW GRANTS").splitlines() if line.strip()]
    except Exception as exc:
        result.inspection_issues.append(f"Privilege inspection failed: {exc}")
        return _persist(result, receipt_root, write_receipt)

    required = {"DROP", "CREATE", "SELECT"}
    for statement in result.artifact_statement_classes:
        capabilities = _STATEMENT_CAPABILITIES.get(statement)
        if capabilities is None:
            result.unknown_requirements.append(statement)
        else:
            required.update(capabilities)
    # Binary logging can add a server-level function-creation requirement that
    # target-scoped grants cannot prove.  Refuse rather than guess.
    if "CREATE FUNCTION" in result.artifact_statement_classes and result.server_conditions.get("log_bin", "").upper() in {"1", "ON"} and result.server_conditions.get("log_bin_trust_function_creators", "").upper() not in {"1", "ON"}:
        result.unknown_requirements.append("CREATE FUNCTION requires server-level binary-log authorization")
    effective = _capabilities(grants, target)
    result.required_capabilities = sorted(required)
    result.effective_capabilities = sorted(effective)
    result.missing_capabilities = [] if "ALL PRIVILEGES" in effective else sorted(required - effective)
    result.passed = not result.inspection_issues and not result.missing_capabilities and not result.unknown_requirements
    return _persist(result, receipt_root, write_receipt)


def _persist(result: RestorePrivilegePreflightResult, receipt_root: Path | None, write_receipt: bool) -> RestorePrivilegePreflightResult:
    if not write_receipt:
        return result
    try:
        return write_restore_preflight_receipt(result, receipt_root=receipt_root)
    except Exception as exc:
        result.inspection_issues.append(f"Preflight evidence write failed: {exc}")
        result.passed = False
        return result
