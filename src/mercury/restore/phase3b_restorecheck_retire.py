"""Governed retirement of the retained Phase 3B restore-check schemas.

Ownership is Mercury restore tooling.  The only DROP helper is
``drop_restorecheck_database(..., allow_phase3b=True)``.  Preview is
read-only apart from a local receipt; apply is a separate, confirmation-gated
step and is not transactional.

Drop order is Permission Intel restore-check, then Erebus restore-check.
The smaller schema is first so a PARTIAL stop still leaves the larger
rehearsal catalog.  ``DROP DATABASE`` is DDL and is not rolled back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from typing import Any, Callable, Protocol

from mercury.core.storage_roles import CONTROL_DIRNAME, DEFAULT_PRIMARY_MOUNT
from mercury.restore.check_cleanup import (
    RestoreCheckCleanupResult,
    assert_restorecheck_database,
    drop_restorecheck_database,
    is_restorecheck_database,
)

OPERATION_ID = "20260722T055400Z_phase3b"
CONFIRMATION = "DROP RESTORECHECK SCHEMAS 20260722T055400Z_PHASE3B"
PAYLOAD_31221_CLASSIFICATION = "PAYLOAD_31221_IRRECOVERABLE"
RESTORECHECK_REQUIRED_FOR_31221 = False

PI_SCHEMA = "_restorecheck_android_permission_intel_20260722T055400Z_phase3b"
EREBUS_SCHEMA = "_restorecheck_erebus_threat_intel_prod_20260722T055400Z_phase3b"
# Smaller catalog first: a PARTIAL failure still preserves the ~2.78 GB copy.
DROP_ORDER = (PI_SCHEMA, EREBUS_SCHEMA)

PROTECTED_NEVER_DROP = frozenset({
    "erebus_threat_intel_prod",
    "erebus_threat_intel_dev",
    "android_permission_intel",
    "android_permission_intel_dev",
})
# Production (and DEV) objects that must not depend on restore-check schemas.
_BLOCKING_DEPENDENCY_SCHEMAS = frozenset(PROTECTED_NEVER_DROP)

DEFAULT_PHASE3B_ROOT = (
    Path(DEFAULT_PRIMARY_MOUNT) / CONTROL_DIRNAME / "phase3b" / OPERATION_ID
)
DEFAULT_BACKUP_ROOT = Path(DEFAULT_PRIMARY_MOUNT) / "mercury_backups"
DEFAULT_RECEIPT_ROOT = (
    Path(DEFAULT_PRIMARY_MOUNT) / CONTROL_DIRNAME / "restorecheck_retirement"
)

_SAFE_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_GRANTEE_PRIV = re.compile(r"^('[^']+'@'[^']+')\s+(.+)$")
PHASE3B_CUTOFF = datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)

DropFn = Callable[[str], RestoreCheckCleanupResult]


class RestorecheckFacts(Protocol):
    def database_names(self) -> set[str]: ...
    def schema_size_bytes(self, name: str) -> int: ...
    def session_count(self, name: str) -> int: ...
    def grants(self, name: str) -> list[str]: ...
    def dependencies(self, name: str) -> list[dict[str, str]]: ...
    def app_connection_targets(self) -> list[str]: ...


@dataclass
class StaticFacts:
    names: set[str]
    sizes: dict[str, int] = field(default_factory=dict)
    sessions: dict[str, int] = field(default_factory=dict)
    grant_map: dict[str, list[str]] = field(default_factory=dict)
    dep_map: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    app_targets: list[str] = field(default_factory=list)

    def database_names(self) -> set[str]:
        return set(self.names)

    def schema_size_bytes(self, name: str) -> int:
        return int(self.sizes.get(name, 0))

    def session_count(self, name: str) -> int:
        return int(self.sessions.get(name, 0))

    def grants(self, name: str) -> list[str]:
        return list(self.grant_map.get(name, []))

    def dependencies(self, name: str) -> list[dict[str, str]]:
        return list(self.dep_map.get(name, []))

    def app_connection_targets(self) -> list[str]:
        return list(self.app_targets)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_ident(name: str) -> str:
    if not _SAFE_IDENT.fullmatch(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def assert_not_protected(name: str) -> None:
    if name in PROTECTED_NEVER_DROP:
        raise ValueError(f"Refusing managed catalog name: {name}")
    if not is_restorecheck_database(name):
        raise ValueError(f"Refusing non-restorecheck schema: {name}")
    assert_restorecheck_database(name)


def proposed_drop_sql(pi_schema: str, erebus_schema: str) -> list[str]:
    for name in (pi_schema, erebus_schema):
        assert_not_protected(name)
    return [
        f"DROP DATABASE `{pi_schema}`;",
        f"DROP DATABASE `{erebus_schema}`;",
    ]


def _dump_record(summary: dict[str, Any], key: str) -> dict[str, Any]:
    dumps = summary.get("dumps")
    if not isinstance(dumps, dict):
        raise ValueError("Phase 3B summary is missing dumps")
    record = dumps.get(key)
    if not isinstance(record, dict):
        raise ValueError(f"Phase 3B summary is missing dump {key}")
    return record


def _later_backup_dirs(backup_root: Path, database: str) -> list[dict[str, str]]:
    from mercury.backup.find_latest_backup import find_backup_directories

    found: list[dict[str, str]] = []
    for directory in find_backup_directories(backup_root, database):
        stamp = directory.name
        try:
            created = datetime.strptime(stamp[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created <= PHASE3B_CUTOFF:
            continue
        dumps = sorted(directory.glob("*.sql.gz"))
        data = [path for path in dumps if ".schema.sql.gz" not in path.name]
        if not data:
            continue
        dump = data[0]
        found.append({
            "directory": str(directory),
            "dump": str(dump),
            "bytes": str(dump.stat().st_size),
        })
    return found


class LiveRestorecheckFacts:
    """Read-only MariaDB inspector for restore-check retirement preview."""

    def __init__(self, query: Callable[[str], str]):
        self._query = query

    def database_names(self) -> set[str]:
        raw = self._query("SHOW DATABASES")
        return {line.strip() for line in raw.splitlines() if line.strip()}

    def schema_size_bytes(self, name: str) -> int:
        ident = _require_ident(name)
        raw = self._query(
            "SELECT IFNULL(SUM(DATA_LENGTH + INDEX_LENGTH), 0) "
            "FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{ident}'"
        )
        text = raw.strip().splitlines()[-1] if raw.strip() else "0"
        return int(float(text))

    def session_count(self, name: str) -> int:
        ident = _require_ident(name)
        raw = self._query(
            "SELECT COUNT(*) FROM information_schema.PROCESSLIST "
            f"WHERE DB = '{ident}'"
        )
        text = raw.strip().splitlines()[-1] if raw.strip() else "0"
        return int(text)

    def grants(self, name: str) -> list[str]:
        ident = _require_ident(name)
        raw = self._query(
            "SELECT CONCAT(GRANTEE, ' ', PRIVILEGE_TYPE) "
            "FROM information_schema.SCHEMA_PRIVILEGES "
            f"WHERE TABLE_SCHEMA = '{ident}' ORDER BY GRANTEE, PRIVILEGE_TYPE"
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def dependencies(self, name: str) -> list[dict[str, str]]:
        ident = _require_ident(name)
        found: list[dict[str, str]] = []
        view_sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.VIEWS "
            f"WHERE TABLE_SCHEMA NOT IN ('{ident}') "
            f"AND VIEW_DEFINITION LIKE '%{ident}%'"
        )
        for line in self._query(view_sql).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                found.append({"kind": "view", "schema": parts[0], "name": parts[1]})
        routine_sql = (
            "SELECT ROUTINE_SCHEMA, ROUTINE_NAME FROM information_schema.ROUTINES "
            f"WHERE ROUTINE_SCHEMA NOT IN ('{ident}') "
            f"AND IFNULL(ROUTINE_DEFINITION, '') LIKE '%{ident}%'"
        )
        for line in self._query(routine_sql).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                found.append({"kind": "routine", "schema": parts[0], "name": parts[1]})
        trigger_sql = (
            "SELECT TRIGGER_SCHEMA, TRIGGER_NAME FROM information_schema.TRIGGERS "
            f"WHERE TRIGGER_SCHEMA NOT IN ('{ident}') "
            f"AND IFNULL(ACTION_STATEMENT, '') LIKE '%{ident}%'"
        )
        for line in self._query(trigger_sql).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                found.append({"kind": "trigger", "schema": parts[0], "name": parts[1]})
        fk_sql = (
            "SELECT CONSTRAINT_SCHEMA, CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
            f"WHERE UNIQUE_CONSTRAINT_SCHEMA = '{ident}' "
            f"AND CONSTRAINT_SCHEMA NOT IN ('{ident}')"
        )
        for line in self._query(fk_sql).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                found.append({"kind": "foreign_key", "schema": parts[0], "name": parts[1]})
        event_sql = (
            "SELECT EVENT_SCHEMA, EVENT_NAME FROM information_schema.EVENTS "
            f"WHERE EVENT_SCHEMA = '{ident}' "
            f"OR IFNULL(EVENT_DEFINITION, '') LIKE '%{ident}%'"
        )
        for line in self._query(event_sql).splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                found.append({"kind": "event", "schema": parts[0], "name": parts[1]})
        return found

    def app_connection_targets(self) -> list[str]:
        names = "', '".join(_require_ident(name) for name in DROP_ORDER)
        raw = self._query(
            "SELECT CONCAT(IFNULL(USER, ''), '@', IFNULL(HOST, ''), ' db=', IFNULL(DB, '')) "
            "FROM information_schema.PROCESSLIST "
            f"WHERE DB IN ('{names}')"
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]


def live_facts_from_config(config) -> LiveRestorecheckFacts:
    from mercury.database.mariadb.client import run_client_query

    return LiveRestorecheckFacts(lambda sql: run_client_query(config, sql))


def _blocking_dependencies(
    facts: RestorecheckFacts, pi_schema: str, erebus_schema: str,
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for name in (pi_schema, erebus_schema):
        if name not in facts.database_names():
            continue
        for item in facts.dependencies(name):
            if item.get("schema") in _BLOCKING_DEPENDENCY_SCHEMAS:
                found.append(item)
    return found


def _parse_grant(item: str) -> tuple[str, str] | None:
    match = _GRANTEE_PRIV.match(item)
    if not match:
        return None
    return match.group(1), match.group(2)


def _has_drop_grant(grants: list[str]) -> bool:
    return any(parsed[1] == "DROP" for item in grants if (parsed := _parse_grant(item)))


def _grant_summary(grants: list[str]) -> dict[str, Any]:
    principals = sorted({parsed[0] for item in grants if (parsed := _parse_grant(item))})
    return {
        "principals": principals,
        "drop_present": _has_drop_grant(grants),
        "count": len(grants),
    }


def _schema_snapshot(facts: RestorecheckFacts, name: str) -> dict[str, Any]:
    exists = name in facts.database_names()
    grants = facts.grants(name) if exists else []
    return {
        "name": name,
        "exists": exists,
        "size_bytes": facts.schema_size_bytes(name) if exists else 0,
        "session_count": facts.session_count(name) if exists else 0,
        "grants": grants,
        "grant_summary": _grant_summary(grants),
        "dependencies": facts.dependencies(name) if exists else [],
    }


def preview_phase3b_restorecheck_retirement(
    *,
    facts: RestorecheckFacts,
    phase3b_root: Path,
    backup_root: Path,
    receipt_root: Path,
    mercury_repo: Path,
    pi_schema: str = PI_SCHEMA,
    erebus_schema: str = EREBUS_SCHEMA,
    recovery_required: bool = RESTORECHECK_REQUIRED_FOR_31221,
    payload_31221: str = PAYLOAD_31221_CLASSIFICATION,
    hostname: str | None = None,
    operator: str | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    """Build a read-only retirement preview. Writes a local receipt only."""
    for name in (pi_schema, erebus_schema):
        assert_not_protected(name)
        if name in PROTECTED_NEVER_DROP:
            raise ValueError(f"DEV/PROD catalogs cannot be retirement targets: {name}")
    if pi_schema == erebus_schema:
        raise ValueError("PI and Erebus restore-check schemas must differ")
    if {pi_schema, erebus_schema} & PROTECTED_NEVER_DROP:
        raise ValueError("protected catalog in retirement set")

    report_path = phase3b_root / "PHASE3B_REPORT.md"
    summary_path = phase3b_root / "phase3b_summary.json"
    blockers: list[str] = []
    classification = "RESTORECHECK_RETIRE_READY"

    if recovery_required:
        classification = "RESTORECHECK_RECOVERY_DEPENDENCY_PRESENT"
        blockers.append("PAYLOAD_31221 recovery still depends on restore-check")

    if not report_path.is_file() or not summary_path.is_file():
        classification = "RESTORECHECK_PHASE3B_EVIDENCE_INCOMPLETE"
        blockers.append("Phase 3B report or summary is missing")
        summary: dict[str, Any] = {}
    else:
        summary = _load_json(summary_path)
        if str(summary.get("run_id") or "") != OPERATION_ID:
            classification = "RESTORECHECK_PHASE3B_EVIDENCE_INCOMPLETE"
            blockers.append("Phase 3B operation ID does not match 20260722T055400Z_phase3b")
        if summary.get("destination_cutover_started") is True:
            classification = "RESTORECHECK_STATE_DRIFT"
            blockers.append("destination cutover already started")
        if summary.get("zero_unexplained_restore_differences") is not True:
            classification = "RESTORECHECK_STATE_DRIFT"
            blockers.append("zero unexplained restore differences is not true")
        retained = summary.get("restore_schemas_retained")
        if not isinstance(retained, list) or set(retained) != set(DROP_ORDER):
            if classification == "RESTORECHECK_RETIRE_READY":
                classification = "RESTORECHECK_PHASE3B_EVIDENCE_INCOMPLETE"
            blockers.append("Phase 3B retained schema list does not match the retirement pair")
        cleanup = str(summary.get("restore_schema_cleanup") or "")
        if "NOT dropped" not in cleanup and classification == "RESTORECHECK_RETIRE_READY":
            classification = "RESTORECHECK_STATE_DRIFT"
            blockers.append("Phase 3B evidence is not the retained-not-dropped cleanup state")

    erebus_dump = None
    pi_dump = None
    try:
        erebus_dump = _dump_record(summary, "erebus_threat_intel_prod") if summary else None
        pi_dump = _dump_record(summary, "android_permission_intel") if summary else None
    except ValueError as exc:
        classification = "RESTORECHECK_PHASE3B_EVIDENCE_INCOMPLETE"
        blockers.append(str(exc))
    for record, label in ((erebus_dump, "erebus"), (pi_dump, "pi")):
        if record is not None and record.get("verified") is not True:
            if classification == "RESTORECHECK_RETIRE_READY":
                classification = "RESTORECHECK_PHASE3B_EVIDENCE_INCOMPLETE"
            blockers.append(f"Phase 3B {label} dump is not marked verified")

    def _bind_dump(record: dict[str, Any] | None, label: str) -> dict[str, Any]:
        nonlocal classification
        if not record:
            return {"label": label, "path": None, "exists": False}
        path = Path(str(record.get("full") or ""))
        expected = str(record.get("full_sha256") or "")
        expected_size = int(record.get("full_size") or 0)
        exists = path.is_file()
        digest = _sha256_file(path) if exists else None
        size = path.stat().st_size if exists else 0
        if not exists:
            if classification == "RESTORECHECK_RETIRE_READY":
                classification = "RESTORECHECK_BACKUP_MISSING"
            blockers.append(f"{label} dump missing: {path}")
        elif digest != expected:
            if classification == "RESTORECHECK_RETIRE_READY":
                classification = "RESTORECHECK_BACKUP_DIGEST_MISMATCH"
            blockers.append(f"{label} dump digest mismatch")
        elif expected_size and size != expected_size:
            if classification == "RESTORECHECK_RETIRE_READY":
                classification = "RESTORECHECK_STATE_DRIFT"
            blockers.append(f"{label} dump size mismatch")
        return {
            "label": label,
            "path": str(path),
            "exists": exists,
            "sha256": digest,
            "expected_sha256": expected,
            "bytes": size,
            "expected_bytes": expected_size,
        }

    erebus_bind = _bind_dump(erebus_dump, "erebus")
    pi_bind = _bind_dump(pi_dump, "pi")

    names = facts.database_names()
    pi_exists = pi_schema in names
    erebus_exists = erebus_schema in names
    if not pi_exists and not erebus_exists and classification == "RESTORECHECK_RETIRE_READY":
        classification = "RESTORECHECK_ALREADY_RETIRED"
    elif (pi_exists != erebus_exists) and classification == "RESTORECHECK_RETIRE_READY":
        classification = "RESTORECHECK_STATE_DRIFT"
        blockers.append("exactly one of the two restore-check schemas is present")

    snapshots = {
        "pi": _schema_snapshot(facts, pi_schema),
        "erebus": _schema_snapshot(facts, erebus_schema),
    }
    sessions = snapshots["pi"]["session_count"] + snapshots["erebus"]["session_count"]
    if sessions and classification == "RESTORECHECK_RETIRE_READY":
        classification = "RESTORECHECK_ACTIVE_CONNECTION"
        blockers.append("active sessions on restore-check schemas")

    prod_deps = _blocking_dependencies(facts, pi_schema, erebus_schema)
    if prod_deps and classification == "RESTORECHECK_RETIRE_READY":
        classification = "RESTORECHECK_DEPENDENCY_FOUND"
        blockers.append("production or DEV object depends on a restore-check schema")

    app_targets = facts.app_connection_targets()
    if app_targets and classification == "RESTORECHECK_RETIRE_READY":
        classification = "RESTORECHECK_ACTIVE_CONNECTION"
        blockers.append("application connections target restore-check schemas")

    later_erebus = _later_backup_dirs(backup_root, "erebus_threat_intel_prod")
    later_pi = _later_backup_dirs(backup_root, "android_permission_intel")
    if (not later_erebus or not later_pi) and classification == "RESTORECHECK_RETIRE_READY":
        classification = "RESTORECHECK_BACKUP_MISSING"
        blockers.append("later Erebus or PI backup coverage is missing")

    for role, snap in snapshots.items():
        if snap["exists"] and not snap["grant_summary"]["drop_present"]:
            if classification == "RESTORECHECK_RETIRE_READY":
                classification = "RESTORECHECK_STATE_DRIFT"
            blockers.append(f"expected DROP grant missing on {role} restore-check schema")

    sql = proposed_drop_sql(pi_schema, erebus_schema)
    reclaim = int(snapshots["pi"]["size_bytes"]) + int(snapshots["erebus"]["size_bytes"])
    preview_id = f"preview_{OPERATION_ID}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    payload = {
        "kind": "phase3b_restorecheck_retire_preview",
        "operation_id": OPERATION_ID,
        "preview_id": preview_id,
        "created_at_utc": _now(),
        "hostname": hostname or socket.gethostname().split(".")[0],
        "git_commit": git_commit or _git_head(mercury_repo),
        "operator": operator or getpass.getuser(),
        "classification": classification,
        "blockers": blockers,
        "drop_order": [pi_schema, erebus_schema],
        "proposed_sql": sql,
        "protected_never_drop": sorted(PROTECTED_NEVER_DROP),
        "schemas": snapshots,
        "expected_reclaim_bytes": reclaim,
        "phase3b": {
            "root": str(phase3b_root),
            "report_path": str(report_path),
            "report_sha256": _sha256_file(report_path) if report_path.is_file() else None,
            "summary_path": str(summary_path),
            "summary_sha256": _sha256_file(summary_path) if summary_path.is_file() else None,
            "destination_cutover_started": summary.get("destination_cutover_started"),
            "zero_unexplained_restore_differences": summary.get(
                "zero_unexplained_restore_differences"
            ),
            "restore_schema_cleanup": summary.get("restore_schema_cleanup"),
        },
        "dumps": {"erebus": erebus_bind, "pi": pi_bind},
        "later_backups": {
            "erebus_threat_intel_prod": later_erebus[-3:],
            "android_permission_intel": later_pi[-3:],
        },
        "payload_31221": {
            "classification": payload_31221,
            "restorecheck_required_for_recovery": bool(recovery_required),
        },
        "confirmation_phrase": CONFIRMATION,
        "ddl_is_transactional": False,
        "drop_helper": "mercury.restore.check_cleanup.drop_restorecheck_database",
    }
    encoded = canonical_json_bytes(payload)
    payload["preview_sha256"] = _sha256_bytes(encoded)
    receipt_root.mkdir(parents=True, exist_ok=True)
    os.chmod(receipt_root, 0o700)
    path = receipt_root / f"{preview_id}.json"
    _write_json(path, payload)
    return {"path": str(path), **payload}


def _preview_digest(payload: dict[str, Any]) -> str:
    document = dict(payload)
    document.pop("preview_sha256", None)
    return _sha256_bytes(canonical_json_bytes(document))


def apply_phase3b_restorecheck_retirement(
    *,
    preview_path: Path,
    confirmation: str,
    facts: RestorecheckFacts,
    drop_fn: DropFn,
    receipt_root: Path,
    expected_preview_sha256: str,
) -> dict[str, Any]:
    """Execute the exact two schema drops after a matching preview. Not transactional."""
    payload = _load_json(preview_path)
    digest = _preview_digest(payload)
    stored = str(payload.get("preview_sha256") or "")
    if stored != digest:
        raise ValueError("preview artifact digest does not match canonical bytes")
    if not expected_preview_sha256 or expected_preview_sha256 != digest:
        raise ValueError("preview SHA-256 does not match --preview-sha256")
    if confirmation != CONFIRMATION:
        raise ValueError("exact confirmation phrase is required")
    if payload.get("operation_id") != OPERATION_ID:
        raise ValueError("preview is not bound to 20260722T055400Z_phase3b")
    if payload.get("classification") != "RESTORECHECK_RETIRE_READY":
        raise ValueError(f"preview is not READY: {payload.get('classification')}")

    pi_schema = payload["drop_order"][0]
    erebus_schema = payload["drop_order"][1]
    for name in (pi_schema, erebus_schema):
        assert_not_protected(name)

    names = facts.database_names()
    if pi_schema not in names and erebus_schema not in names:
        receipt = _apply_receipt(
            payload, digest, started=_now(), completed=_now(),
            results=[], classification="RESTORECHECK_ALREADY_RETIRED",
            note="both schemas already absent; no DROP issued",
            facts=facts, pi_schema=pi_schema, erebus_schema=erebus_schema,
        )
        path = _write_apply_receipt(receipt_root, receipt)
        return {"path": str(path), **receipt}

    if payload["classification"] == "RESTORECHECK_RETIRE_READY":
        if pi_schema not in names or erebus_schema not in names:
            raise ValueError("REFUSE BEFORE DROP: schema existence drifted")
        if facts.session_count(pi_schema) or facts.session_count(erebus_schema):
            raise ValueError("REFUSE BEFORE DROP: active sessions")
        if facts.app_connection_targets():
            raise ValueError("REFUSE BEFORE DROP: application connections")
        if _blocking_dependencies(facts, pi_schema, erebus_schema):
            raise ValueError("REFUSE BEFORE DROP: production or DEV dependency found")
        current_reclaim = facts.schema_size_bytes(pi_schema) + facts.schema_size_bytes(erebus_schema)
        if int(payload.get("expected_reclaim_bytes") or 0) != current_reclaim:
            raise ValueError("REFUSE BEFORE DROP: schema size drifted")
        for item in payload["dumps"].values():
            path = Path(str(item["path"]))
            if not path.is_file() or _sha256_file(path) != item["expected_sha256"]:
                raise ValueError("REFUSE BEFORE DROP: backup digest changed")
        for group in (payload.get("later_backups") or {}).values():
            for item in group:
                later = Path(str(item.get("dump") or ""))
                if not later.is_file():
                    raise ValueError("REFUSE BEFORE DROP: later backup missing")

    started = _now()
    results: list[dict[str, Any]] = []
    remaining = [pi_schema, erebus_schema]
    classification = "RESTORECHECK_SCHEMAS_RETIRED"
    note = ""
    for index, schema in enumerate(remaining):
        pre_exists = schema in facts.database_names()
        pre_size = facts.schema_size_bytes(schema) if pre_exists else 0
        drop_result = drop_fn(schema)
        post_names = facts.database_names()
        post_exists = schema in post_names
        row = {
            "schema": schema,
            "pre_drop_existence": pre_exists,
            "pre_drop_size_bytes": pre_size,
            "drop_refused": bool(drop_result.refused),
            "drop_dropped": bool(drop_result.dropped),
            "drop_message": drop_result.message,
            "post_drop_existence": post_exists,
        }
        results.append(row)
        failed = drop_result.refused or not drop_result.dropped or post_exists
        if failed:
            if index == 0:
                classification = "RESTORECHECK_STATE_DRIFT"
                note = "first DROP failed; second DROP not attempted"
            else:
                classification = "RESTORECHECK_RETIRE_PARTIAL"
                note = "first schema dropped; second DROP failed or outcome unknown"
            break

    completed = _now()
    receipt = _apply_receipt(
        payload, digest, started=started, completed=completed,
        results=results, classification=classification, note=note,
        facts=facts, pi_schema=pi_schema, erebus_schema=erebus_schema,
    )
    path = _write_apply_receipt(receipt_root, receipt)
    return {"path": str(path), **receipt}


def _apply_receipt(
    preview: dict[str, Any],
    digest: str,
    *,
    started: str,
    completed: str,
    results: list[dict[str, Any]],
    classification: str,
    note: str,
    facts: RestorecheckFacts,
    pi_schema: str,
    erebus_schema: str,
) -> dict[str, Any]:
    names = facts.database_names()
    return {
        "kind": "phase3b_restorecheck_retire_receipt",
        "operation_id": OPERATION_ID,
        "preview_id": preview.get("preview_id"),
        "preview_sha256": digest,
        "operator": preview.get("operator"),
        "hostname": preview.get("hostname"),
        "git_commit": preview.get("git_commit"),
        "started_at_utc": started,
        "completed_at_utc": completed,
        "confirmation_phrase": CONFIRMATION,
        "ddl_is_transactional": False,
        "schemas": results,
        "observed_post_drop_schema_state": {
            "pi": {"name": pi_schema, "exists": pi_schema in names},
            "erebus": {"name": erebus_schema, "exists": erebus_schema in names},
        },
        "phase3b_evidence_digests": {
            "report_sha256": (preview.get("phase3b") or {}).get("report_sha256"),
            "summary_sha256": (preview.get("phase3b") or {}).get("summary_sha256"),
        },
        "dumps": preview.get("dumps"),
        "payload_31221": preview.get("payload_31221"),
        "expected_reclaim_bytes": preview.get("expected_reclaim_bytes"),
        "note": note,
        "final_classification": classification,
    }


def _write_apply_receipt(root: Path, payload: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"apply_{OPERATION_ID}_{stamp}.json"
    _write_json(path, payload)
    return path


def default_drop_fn(config=None) -> DropFn:
    def _drop(name: str) -> RestoreCheckCleanupResult:
        return drop_restorecheck_database(
            name,
            execute=True,
            allow_phase3b=True,
            if_exists=False,
            config=config,
        )
    return _drop


__all__ = [
    "CONFIRMATION",
    "DROP_ORDER",
    "EREBUS_SCHEMA",
    "LiveRestorecheckFacts",
    "OPERATION_ID",
    "PAYLOAD_31221_CLASSIFICATION",
    "PI_SCHEMA",
    "PROTECTED_NEVER_DROP",
    "StaticFacts",
    "apply_phase3b_restorecheck_retirement",
    "default_drop_fn",
    "live_facts_from_config",
    "preview_phase3b_restorecheck_retirement",
    "proposed_drop_sql",
]
