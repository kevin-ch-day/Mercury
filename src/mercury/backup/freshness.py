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
            "schema_migrations.applied_at",
            "SELECT MAX(applied_at) FROM obsidiandroid_core_prod.schema_migrations "
            "WHERE applied_at IS NOT NULL",
        ),
    ],
}

OPERATOR_FRESHNESS_GUIDANCE = (
    "Artifact verified means backup files pass checksum/manifest checks. "
    "Freshness compares the backup timestamp to latest read-only source DB activity. "
    "Run full backup before workstation handoff when freshness is stale or unknown."
)


def backup_entry_status_label(entry) -> str:
    """Operator-facing backup row status for menus and recovery screens."""
    if entry is None:
        return "Missing"
    protection_status = getattr(entry, "protection_status", None)
    if protection_status != "verified":
        if protection_status == "missing":
            return "Missing"
        if protection_status == "failed":
            return "Unverified"
        return "Warning"
    freshness = getattr(entry, "freshness", None)
    if freshness == FRESHNESS_STALE:
        return "Stale"
    if freshness == FRESHNESS_UNKNOWN:
        return "Unknown"
    return "Fresh"


def menu_handoff_problem_summary(problem_parts: list[str]) -> str:
    return (
        "Fresh full backup needed before workstation handoff: "
        + ", ".join(problem_parts)
        + "."
    )


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
        "failed": "unverified",
        "untrusted root": "untrusted",
    }
    return mapping.get(protection_status, "warning")


def freshness_status_label(freshness: str) -> str:
    if freshness == FRESHNESS_FRESH:
        return "fresh"
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
        f"{' and '.join(parts)} verified backup(s) — bundle documents current USB state "
        "but handoff should wait for fresh full backups."
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
) -> BackupFreshnessAssessment:
    """
    Compare backup timestamp against latest read-only source DB activity.

    Conservative: when activity cannot be determined, freshness stays unknown.
    """
    assessment = BackupFreshnessAssessment(database=database, backup_at=backup_at)
    assessment.backup_age = format_backup_age(backup_at, now=now)

    if backup_at is None:
        assessment.notes.append("Backup timestamp unavailable; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment

    if not live or not should_probe_database_status():
        assessment.notes.append("Live source activity not probed; freshness unknown.")
        assessment.recommend_full_backup = True
        return assessment

    cfg = config or try_load_mariadb_config()
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
