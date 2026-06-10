"""Read-only restore readiness and live target catalog completeness checks."""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.layout import MANIFEST_FILENAME
from mercury.backup.verification import verify_backup_artifacts
from mercury.core.execution_policy import load_execution_policy
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.core.runtime import should_probe_database_status
from mercury.database.core import classify_database
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.session import readonly_scalars, try_load_mariadb_config

CREATE_TABLE_RE = re.compile(r"CREATE TABLE [`']([^`']+)[`']", re.IGNORECASE)
CREATE_VIEW_RE = re.compile(r"CREATE(?:\s+OR\s+REPLACE)?\s+(?:ALGORITHM=\w+\s+)?(?:DEFINER=[^\s]+\s+)?(?:SQL\s+SECURITY\s+\w+\s+)?VIEW [`']([^`']+)[`']", re.IGNORECASE)

TARGET_COMPLETENESS_SCOPE_NOTE = (
    "Target/schema completeness only: compares live object counts and critical tables "
    "against the latest verified backup structure. This is not backup data freshness."
)

# Erebus primary catalog tables required for VT batch and migration tracking.
EREBUS_CANONICAL_TABLES: tuple[str, ...] = (
    "schema_migrations",
    "malware_sample_catalog",
    "malware_artifact_hash_registry",
    "virustotal_sample_state",
    "virustotal_run_ledger",
)

CANONICAL_TABLES_BY_DATABASE: dict[str, tuple[str, ...]] = {
    "erebus_threat_intel_prod": EREBUS_CANONICAL_TABLES,
}


class TargetCompletenessEntry(BaseModel):
    """Compare live target catalog against the latest verified backup baseline."""

    database: str
    backup_id: str | None = None
    backup_directory: str | None = None
    backup_verified: bool = False
    backup_table_count: int | None = None
    backup_view_count: int | None = None
    backup_object_count: int | None = None
    live_connected: bool = False
    live_exists: bool = False
    live_table_count: int | None = None
    live_view_count: int | None = None
    live_object_count: int | None = None
    missing_critical_tables: list[str] = Field(default_factory=list)
    completeness_status: str = "unknown"
    ready_for_restore_planning: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TargetCompletenessReport(BaseModel):
    mode: str
    backup_root: str
    entries: list[TargetCompletenessEntry] = Field(default_factory=list)
    complete_count: int = 0
    incomplete_count: int = 0
    unknown_count: int = 0


def canonical_tables_for(database: str) -> tuple[str, ...]:
    return CANONICAL_TABLES_BY_DATABASE.get(database, ())


def _sql_escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def _open_text_artifact(path: Path):
    if path.suffix == ".gz" or path.name.endswith(".sql.gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_schema_artifact(path: Path) -> tuple[int, int, set[str]]:
    """Return table count, view count, and base table names from a schema/full dump artifact."""
    tables: set[str] = set()
    view_count = 0
    if not path.is_file():
        return 0, 0, tables

    with _open_text_artifact(path) as handle:
        for line in handle:
            table_match = CREATE_TABLE_RE.search(line)
            if table_match:
                tables.add(table_match.group(1))
                continue
            if CREATE_VIEW_RE.search(line):
                view_count += 1

    return len(tables), view_count, tables


def _resolve_schema_artifact(backup_dir: Path, manifest_data: dict[str, object]) -> Path | None:
    schema_name = manifest_data.get("schema_file")
    if isinstance(schema_name, str) and schema_name:
        schema_path = backup_dir / schema_name
        if schema_path.is_file():
            return schema_path

    dump_name = manifest_data.get("dump_file")
    if isinstance(dump_name, str) and dump_name:
        dump_path = backup_dir / dump_name
        if dump_path.is_file():
            return dump_path
    return None


def _load_manifest_payload(backup_dir: Path) -> dict[str, object]:
    manifest_path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def fetch_live_base_table_names(
    database: str,
    config: MariaDbConnectionConfig,
    *,
    scalars_fn=None,
) -> list[str]:
    """Read-only list of base table names for a database."""
    fetch = scalars_fn or readonly_scalars
    escaped = _sql_escape_literal(database)
    sql = (
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{escaped}' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    )
    return fetch(config, sql)


def build_target_completeness_entry(
    database: str,
    *,
    live: bool = True,
    config: MariaDbConnectionConfig | None = None,
    inspect_fn=None,
    scalars_fn=None,
) -> TargetCompletenessEntry:
    """
    Compare live target catalog counts against the latest verified backup baseline.

    Read-only: inspects information_schema and parses backup artifacts only.
    """
    classification = classify_database(database)
    policy = load_execution_policy()
    entry = TargetCompletenessEntry(database=database)
    entry.notes.append("Read-only target completeness check; no restore executed.")

    if not classification.backup_source:
        entry.completeness_status = "not_applicable"
        entry.blockers.append(f"'{database}' is not an approved production backup source.")
        return entry

    if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
        entry.completeness_status = "unknown"
        entry.warnings.append(
            "Backup root is repo-local fallback; configure USB-backed backups before deployment checks."
        )

    backup_dir = find_latest_backup_directory(policy.backup_root, database)
    if backup_dir is None:
        entry.completeness_status = "backup_unavailable"
        entry.blockers.append("No on-disk backup found for production source.")
        return entry

    verify = verify_backup_artifacts(
        backup_dir,
        database=database,
        backup_kind=BACKUP_KIND_FULL,
    )
    entry.backup_directory = str(backup_dir)
    entry.backup_id = verify.backup_id
    entry.backup_verified = verify.verified
    if not verify.verified:
        entry.completeness_status = "backup_unverified"
        entry.blockers.append("Latest backup is not artifact-verified (manifest/checksum/size/role).")
        return entry

    manifest_data = _load_manifest_payload(backup_dir)
    schema_artifact = _resolve_schema_artifact(backup_dir, manifest_data)
    if schema_artifact is None:
        entry.completeness_status = "backup_unavailable"
        entry.blockers.append("Latest verified backup is missing schema/full dump artifacts.")
        return entry

    backup_tables, backup_views, backup_table_names = parse_schema_artifact(schema_artifact)
    entry.backup_table_count = backup_tables
    entry.backup_view_count = backup_views
    entry.backup_object_count = backup_tables + backup_views

    probe_live = live and should_probe_database_status()
    if not probe_live:
        entry.completeness_status = "unknown"
        entry.notes.append("Live MariaDB target not probed; backup baseline recorded only.")
        entry.ready_for_restore_planning = True
        return entry

    cfg = config or try_load_mariadb_config()
    if cfg is None:
        entry.completeness_status = "unknown"
        entry.warnings.append("MariaDB not configured; cannot compare live target catalog.")
        entry.ready_for_restore_planning = True
        return entry

    inspect = inspect_fn or inspect_database_on_server
    inspect_result = inspect(database, cfg)
    if inspect_result.error:
        entry.completeness_status = "unknown"
        entry.warnings.append(f"Live inspect failed: {inspect_result.error}")
        return entry

    entry.live_connected = inspect_result.connected
    entry.live_exists = inspect_result.exists_on_server
    if not inspect_result.exists_on_server:
        entry.completeness_status = "missing_target"
        entry.blockers.append(f"Target database '{database}' not found on server.")
        return entry

    entry.live_table_count = inspect_result.table_count
    entry.live_view_count = inspect_result.view_count
    if inspect_result.table_count is not None and inspect_result.view_count is not None:
        entry.live_object_count = inspect_result.table_count + inspect_result.view_count

    canonical = canonical_tables_for(database)
    if canonical:
        try:
            live_tables = set(fetch_live_base_table_names(database, cfg, scalars_fn=scalars_fn))
        except Exception as exc:  # noqa: BLE001 — surface as warning, keep read-only path
            entry.warnings.append(f"Could not list live base tables: {exc}")
            live_tables = set()
        entry.missing_critical_tables = [name for name in canonical if name not in live_tables]

    blockers: list[str] = []
    if (
        entry.live_table_count is not None
        and entry.backup_table_count is not None
        and entry.live_table_count < entry.backup_table_count
    ):
        blockers.append(
            "Target table count "
            f"({entry.live_table_count}) is below verified backup baseline ({entry.backup_table_count})."
        )
    if (
        entry.live_view_count is not None
        and entry.backup_view_count is not None
        and entry.live_view_count < entry.backup_view_count
    ):
        blockers.append(
            "Target view count "
            f"({entry.live_view_count}) is below verified backup baseline ({entry.backup_view_count})."
        )
    if entry.missing_critical_tables:
        joined = ", ".join(entry.missing_critical_tables)
        blockers.append(f"Critical tables missing on live target: {joined}.")

    entry.blockers.extend(blockers)
    if blockers:
        entry.completeness_status = "incomplete"
        entry.ready_for_restore_planning = False
        if (
            entry.live_object_count is not None
            and entry.backup_object_count is not None
            and entry.live_object_count < entry.backup_object_count // 2
        ):
            entry.warnings.append(
                "Connection OK but catalog appears severely incomplete relative to backup baseline "
                "(common after partial transfer/restore)."
            )
    else:
        entry.completeness_status = "complete"
        entry.ready_for_restore_planning = True

    from mercury.logging.events import log_target_completeness

    log_target_completeness(
        database=database,
        status=entry.completeness_status,
        live_objects=entry.live_object_count,
        backup_objects=entry.backup_object_count,
        missing_critical=len(entry.missing_critical_tables),
    )
    return entry


def build_target_completeness_report(
    *,
    databases: list[str] | None = None,
    live: bool = True,
    config: MariaDbConnectionConfig | None = None,
) -> TargetCompletenessReport:
    """Build completeness entries for active backup sources."""
    from mercury.backup.batch_runner import resolve_batch_sources

    policy = load_execution_policy()
    probe = live and should_probe_database_status()
    names = databases or resolve_batch_sources(live=probe)
    entries = [
        build_target_completeness_entry(name, live=live, config=config)
        for name in names
    ]
    complete = sum(1 for entry in entries if entry.completeness_status == "complete")
    incomplete = sum(1 for entry in entries if entry.completeness_status == "incomplete")
    unknown = len(entries) - complete - incomplete
    return TargetCompletenessReport(
        mode="live" if probe else "offline",
        backup_root=str(policy.backup_root),
        entries=entries,
        complete_count=complete,
        incomplete_count=incomplete,
        unknown_count=unknown,
    )


LIVE_READINESS_FAILURE_STATUSES: frozenset[str] = frozenset(
    {
        "incomplete",
        "backup_unverified",
        "backup_unavailable",
        "missing_target",
    }
)


def restore_readiness_should_fail(report: TargetCompletenessReport, *, live: bool) -> bool:
    """Return True when live deployment checks should exit non-zero."""
    if report.incomplete_count:
        return True
    if not live:
        return False
    return any(entry.completeness_status in LIVE_READINESS_FAILURE_STATUSES for entry in report.entries)
