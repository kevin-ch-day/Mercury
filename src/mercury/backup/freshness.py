"""Backup freshness assessment — artifact verified vs source DB recency."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, Field

from mercury.core.runtime import should_probe_database_status
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.session import readonly_scalar, try_load_mariadb_config
from mercury.terminal.format import format_human_datetime

FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_UNKNOWN = "unknown"
FRESHNESS_EMPTY = "empty"

ScalarFn = Callable[[MariaDbConnectionConfig, str], str]

# Read-only activity probes: (signal label, SQL returning one timestamp).
SOURCE_ACTIVITY_PROBES: dict[str, list[tuple[str, str]]] = {
    "erebus_threat_intel_prod": [
        (
            "virustotal_run_ledger.finished_at_utc",
            "SELECT MAX(finished_at_utc) FROM erebus_threat_intel_prod.virustotal_run_ledger "
            "WHERE finished_at_utc IS NOT NULL",
        ),
        (
            "virustotal_sample_state.record_updated_at_utc",
            "SELECT MAX(record_updated_at_utc) FROM erebus_threat_intel_prod.virustotal_sample_state "
            "WHERE record_updated_at_utc IS NOT NULL",
        ),
    ],
    "android_permission_intel": [
        (
            "android_permission_enrich_vt_event.ingested_at_utc",
            "SELECT MAX(ingested_at_utc) FROM android_permission_intel.android_permission_enrich_vt_event "
            "WHERE ingested_at_utc IS NOT NULL",
        ),
        (
            "android_permission_enrich_vt_current.record_updated_at_utc",
            "SELECT MAX(record_updated_at_utc) FROM android_permission_intel.android_permission_enrich_vt_current "
            "WHERE record_updated_at_utc IS NOT NULL",
        ),
    ],
    "scytaledroid_core_prod": [
        (
            "android_apk_repository.updated_at",
            "SELECT MAX(updated_at) FROM scytaledroid_core_prod.android_apk_repository "
            "WHERE updated_at IS NOT NULL",
        ),
        (
            "analysis_derivation_receipts.finished_at_utc",
            "SELECT MAX(finished_at_utc) FROM scytaledroid_core_prod.analysis_derivation_receipts "
            "WHERE finished_at_utc IS NOT NULL",
        ),
    ],
    "obsidiandroid_core_prod": [
        (
            "core_schema_migration.applied_at_utc",
            "SELECT MAX(applied_at_utc) FROM obsidiandroid_core_prod.core_schema_migration "
            "WHERE applied_at_utc IS NOT NULL",
        ),
        (
            "core_artifact.imported_at_utc",
            "SELECT MAX(imported_at_utc) FROM obsidiandroid_core_prod.core_artifact "
            "WHERE imported_at_utc IS NOT NULL",
        ),
        (
            "core_run.run_completed_at_utc",
            "SELECT MAX(run_completed_at_utc) FROM obsidiandroid_core_prod.core_run "
            "WHERE run_completed_at_utc IS NOT NULL",
        ),
    ],
}

OPERATOR_FRESHNESS_GUIDANCE = (
    "Artifact verified means backup files pass checksum/manifest checks. "
    "Freshness compares the backup timestamp to latest read-only source DB activity. "
    "Run full backup before workstation handoff when freshness is stale or unknown."
)


def backup_entry_verify_label(entry) -> str:
    """Integrity/verification column for the exact displayed backup ID.

    Restore-check labels apply only when ``restore_check_status`` was bound to
    this entry's ``backup_id`` (see :func:`mercury.backup.status.build_backup_status_report`).
    Missing restore-check never inherits another backup's result.
    """
    if entry is None:
        return "Missing"
    protection_status = getattr(entry, "protection_status", None)
    restore_status = getattr(entry, "restore_check_status", None)
    backup_id = getattr(entry, "backup_id", None)
    restore_backup_id = getattr(entry, "restore_check_backup_id", None)
    restore_matches = bool(
        restore_status
        and backup_id
        and restore_backup_id
        and backup_id == restore_backup_id
    )
    if restore_matches and restore_status == "passed":
        stamp = getattr(entry, "manifest_verification_stamp", None)
        if stamp is False:
            return "RC passed · unstamped"
        return "Restore-check passed"
    if restore_matches and restore_status in {"failed", "verification_failed"}:
        return "Restore-check failed"
    if protection_status == "verified":
        stamp = getattr(entry, "manifest_verification_stamp", None)
        unstamped = stamp is False
        # Mismatched or missing restore-check must not look fully verified.
        if backup_id and not restore_matches:
            return "OK* · no RC" if unstamped else "Not restore-checked"
        if unstamped:
            return "OK unstamped"
        return "Verified"
    if protection_status == "missing":
        return "Missing"
    if protection_status == "absent":
        return "Absent"
    if protection_status == "failed":
        return "Verify failed"
    if protection_status == "untrusted root":
        return "Missing manifest"
    return "Unverified"


def backup_entry_freshness_label(entry) -> str:
    """Freshness column — independent of verification."""
    if entry is None:
        return "—"
    protection_status = getattr(entry, "protection_status", None)
    if protection_status in {"missing", "absent"}:
        return "—"
    freshness = getattr(entry, "freshness", None)
    if freshness == FRESHNESS_EMPTY:
        return "Empty"
    if freshness == FRESHNESS_STALE:
        return "Stale"
    if freshness == FRESHNESS_FRESH:
        return "Fresh"
    return "Unknown"


def backup_entry_artifact_label(entry) -> str:
    """Artifact-integrity label only — never includes restore-check state."""
    if entry is None:
        return "Missing"
    protection_status = getattr(entry, "protection_status", None)
    if protection_status == "verified":
        stamp = getattr(entry, "manifest_verification_stamp", None)
        if stamp is False:
            return "OK unstamped"
        return "Verified"
    if protection_status == "missing":
        return "Missing"
    if protection_status == "absent":
        return "Absent"
    if protection_status == "failed":
        return "Failed"
    if protection_status == "untrusted root":
        return "No manifest"
    return "Unverified"


def backup_entry_status_label(entry) -> str:
    """Combined operator-facing status for compact recovery screens.

    Prefer separate freshness/verify columns on the Backup Operations table.
    Freshness gaps outrank a restore-check/artifact pass so stale sources stay
    visible, but integrity failures still win over freshness.
    """
    if entry is None:
        return "Missing"
    verify = backup_entry_verify_label(entry)
    if verify in {
        "Verify failed",
        "Missing",
        "Absent",
        "Missing manifest",
        "Unverified",
        "Restore-check failed",
    }:
        if verify == "Verify failed":
            return "Unverified"
        if verify == "Missing manifest":
            return "Warning"
        return verify
    freshness = backup_entry_freshness_label(entry)
    if freshness in {"Stale", "Unknown", "Empty"}:
        return freshness
    if verify != "Verified":
        return verify
    return freshness


def menu_handoff_problem_summary(problem_parts: list[str]) -> str:
    """Operator warning for Backup Operations gaps before handoff.

    Chooses the lead phrase from the actual gap types so a restore-check-only
    backlog is never described as needing another full backup.
    """
    joined = ", ".join(problem_parts)
    lowered = [part.lower() for part in problem_parts]
    restore_only = bool(lowered) and all(
        "not restore-checked" in part
        or "restore-check failed" in part
        or "no rc" in part
        for part in lowered
    )
    stamp_only = bool(lowered) and all(
        "artifact ok" in part
        or "ok unstamped" in part
        or "ok* · no rc" in part
        or "rc passed · unstamped" in part
        for part in lowered
    )
    backup_gaps = any(
        token in part
        for part in lowered
        for token in (
            "stale",
            "unknown",
            "missing",
            "unverified",
            "verify failed",
            "missing manifest",
            "absent from server",
        )
    )
    empty_only = bool(lowered) and all("empty" in part for part in lowered)
    # Unstamped + no RC is both a stamp and restore-check gap.
    if stamp_only and any("no rc" in part for part in lowered):
        return f"Manifest stamp / restore-check pending before workstation handoff: {joined}."
    if restore_only:
        return f"Restore-check required before workstation handoff: {joined}."
    if stamp_only:
        return f"Manifest stamp pending before workstation handoff: {joined}."
    if empty_only:
        return (
            f"Empty source schema(s) on server — preserve with one verified backup "
            f"before workstation handoff: {joined}."
        )
    if backup_gaps and any(
        "restore-check" in part or "not restore-checked" in part or "no rc" in part
        for part in lowered
    ):
        return f"Before workstation handoff: {joined}."
    if any("empty" in part for part in lowered) and backup_gaps:
        return f"Before workstation handoff: {joined}."
    if any("empty" in part for part in lowered):
        return (
            f"Empty source schema(s) on server — preserve with one verified backup "
            f"before workstation handoff: {joined}."
        )
    if backup_gaps:
        return f"Fresh full backup needed before workstation handoff: {joined}."
    return f"Before workstation handoff: {joined}."


def protection_handoff_action_item(*, include_sync: bool = True) -> str:
    message = (
        "Run full backup for stale or unknown-freshness sources before workstation handoff"
    )
    if include_sync:
        return message + " or prod→dev sync."
    return message + "."


class BackupFreshnessAssessment(BaseModel):
    database: str
    backup_at: datetime | None = None
    latest_source_activity_at: datetime | None = None
    activity_signal: str | None = None
    freshness: str = FRESHNESS_UNKNOWN
    backup_age: str | None = None
    recommend_full_backup: bool = False
    notes: list[str] = Field(default_factory=list)


def parse_db_timestamp(raw: str | None) -> datetime | None:
    """Parse MariaDB/Erebus timestamp strings into aware UTC datetimes."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.startswith("0000-00-00") or text.startswith("1970-01-01"):
        return None

    normalized = text.replace(" UTC", "+00:00").replace("Z", "+00:00")
    if "T" not in normalized and "+" not in normalized and len(normalized) >= 19:
        normalized = normalized.replace(" ", "T", 1) + "+00:00"

    for candidate in (normalized, text):
        try:
            instant = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            return instant.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def parse_backup_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        instant = value
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return instant.astimezone(timezone.utc)
    return parse_db_timestamp(str(value))


def format_backup_age(backup_at: datetime | None, *, now: datetime | None = None) -> str | None:
    if backup_at is None:
        return None
    reference = now or datetime.now(timezone.utc)
    delta = reference - backup_at.astimezone(timezone.utc)
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < 3600:
        minutes = max(seconds // 60, 1)
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = max(seconds // 3600, 1)
        return f"{hours}h ago"
    days = max(seconds // 86400, 1)
    return f"{days}d ago"


def artifact_status_label(protection_status: str) -> str:
    """Map protection status to operator-facing artifact column label."""
    mapping = {
        "verified": "verified",
        "missing": "missing",
        "absent": "absent",
        "failed": "unverified",
        "untrusted root": "untrusted",
    }
    return mapping.get(protection_status, "warning")


def freshness_status_label(freshness: str) -> str:
    if freshness == FRESHNESS_FRESH:
        return "fresh"
    if freshness == FRESHNESS_EMPTY:
        return "empty"
    if freshness == FRESHNESS_STALE:
        return "stale"
    return "unknown"


def display_artifact_status_label(protection_status: str) -> str:
    return artifact_status_label(protection_status).title()


def display_freshness_label(freshness: str | None) -> str:
    if not freshness:
        return "—"
    return freshness_status_label(freshness).title()


def backup_stale_handoff_blocker(
    database: str,
    *,
    backup_at: datetime | None,
    live: bool = True,
) -> str | None:
    """Return a blocker message when a verified backup is stale for live operations."""
    if not live:
        return None
    assessment = assess_backup_freshness(database, backup_at=backup_at, live=live)
    if assessment.freshness == FRESHNESS_STALE:
        return (
            f"Latest verified backup for '{database}' is stale relative to live source activity."
        )
    return None


def handoff_freshness_warning(*, stale_count: int = 0, unknown_count: int = 0) -> str | None:
    if not stale_count and not unknown_count:
        return None
    parts: list[str] = []
    if stale_count:
        parts.append(f"{stale_count} stale")
    if unknown_count:
        parts.append(f"{unknown_count} unknown freshness")
    return (
        f"{' and '.join(parts)} source(s) require attention — bundle documents current "
        "operator-storage state but handoff should wait for fresh full backups."
    )


def fetch_latest_source_activity_at(
    database: str,
    config: MariaDbConnectionConfig,
    *,
    scalar_fn: ScalarFn | None = None,
) -> tuple[datetime | None, str | None]:
    """Read-only latest activity timestamp for a source database."""
    fetch = scalar_fn or readonly_scalar
    probes = SOURCE_ACTIVITY_PROBES.get(database, [])
    latest: datetime | None = None
    signals: list[str] = []

    for signal, sql in probes:
        try:
            raw = fetch(config, sql)
        except (MariaDbLiveError, OSError, ValueError):
            continue
        parsed = parse_db_timestamp(raw)
        if parsed is None:
            continue
        if latest is None or parsed > latest:
            latest = parsed
        signals.append(signal)

    if latest is None:
        return None, None
    if len(signals) == 1:
        return latest, signals[0]
    return latest, "combined activity signals"


def assess_backup_freshness(
    database: str,
    *,
    backup_at: datetime | None,
    live: bool = True,
    config: MariaDbConnectionConfig | None = None,
    scalar_fn: ScalarFn | None = None,
    now: datetime | None = None,
    source_is_empty: bool = False,
) -> BackupFreshnessAssessment:
    """
    Compare backup timestamp against latest read-only source DB activity.

    Conservative: when activity cannot be determined, freshness stays unknown.

    Callers decide whether live probing is allowed (``live=True``). When an
    explicit ``scalar_fn`` is injected (tests or custom probes), the global
    ``should_probe_database_status()`` gate does **not** block evaluation —
    the injected probe is treated as already authorized by the caller.
    Production callers that omit ``scalar_fn`` still fail closed when the
    runtime gate disables live probes.
    """
    assessment = BackupFreshnessAssessment(database=database, backup_at=backup_at)
    assessment.backup_age = format_backup_age(backup_at, now=now)

    if backup_at is None:
        assessment.notes.append("Backup timestamp unavailable; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment

    # A live schema with no tables or views has no meaningful application
    # activity timestamp.  Its verified artifact is still important: it
    # recreates the intentionally present empty schema on the receiver.
    if source_is_empty:
        assessment.freshness = FRESHNESS_EMPTY
        assessment.notes.append(
            "Live database has no tables or views; verified backup preserves an empty schema."
        )
        return assessment

    injected_probe = scalar_fn is not None
    if not live:
        assessment.notes.append("Live source activity not probed; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment
    if not injected_probe and not should_probe_database_status():
        assessment.notes.append("Live source activity not probed; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment

    cfg = config or try_load_mariadb_config()
    if cfg is None and not injected_probe:
        assessment.notes.append("MariaDB not configured; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment
    if cfg is None and injected_probe:
        # Tests may pass a sentinel config object; fall through with that object.
        cfg = config  # type: ignore[assignment]
    if cfg is None:
        assessment.notes.append("MariaDB not configured; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment

    activity_at, signal = fetch_latest_source_activity_at(database, cfg, scalar_fn=scalar_fn)
    assessment.latest_source_activity_at = activity_at
    assessment.activity_signal = signal

    if activity_at is None:
        assessment.notes.append("No reliable source activity timestamp found; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment

    backup_instant = backup_at.astimezone(timezone.utc)
    activity_instant = activity_at.astimezone(timezone.utc)
    if activity_instant <= backup_instant:
        assessment.freshness = FRESHNESS_FRESH
        assessment.notes.append(
            f"Latest source activity ({format_human_datetime(activity_instant.isoformat())}) "
            f"is not newer than backup ({format_human_datetime(backup_instant.isoformat())})."
        )
        return assessment

    assessment.freshness = FRESHNESS_STALE
    assessment.recommend_full_backup = True
    assessment.notes.append(
        f"Source activity after backup detected via {signal}: "
        f"{format_human_datetime(activity_instant.isoformat())} > "
        f"{format_human_datetime(backup_instant.isoformat())}."
    )
    return assessment
