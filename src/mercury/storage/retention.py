"""Retention / destination-package protection policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from mercury.core.paths import CONFIG_DIR, REPO_ROOT

RETENTION_EXAMPLE = CONFIG_DIR / "retention.example.toml"
RETENTION_LOCAL = CONFIG_DIR / "retention.toml"

DEFAULT_PROTECTED_RUN_IDS: tuple[str, ...] = ("20260722T055400Z_phase3b",)
DEFAULT_PROTECTED_BACKUP_IDS: tuple[str, ...] = (
    "erebus_threat_intel_prod-full-20260722_055507_238",
    "android_permission_intel-full-20260722_055648_287",
)
DEFAULT_PROTECTED_CAPTURE_IDS: tuple[str, ...] = (
    "erebus_destination_candidate_3f1bb5b_20260722T150930Z",
)
DEFAULT_MANUAL_REVIEW_ROOTS: tuple[str, ...] = (
    "scytaledroid_migration_checkpoints",
    "scytaledroid_apk_store_backups",
    "scytaledroid_artifacts",
)
DEFAULT_EXCLUDE_DESTINATION: tuple[str, ...] = (
    *DEFAULT_MANUAL_REVIEW_ROOTS,
    "mercury_repo_clones",
)
DEFAULT_GOVERNED_ROOTS: tuple[str, ...] = (
    "mercury_backups",
    "mercury_worktree_snapshots",
    "mercury_repo_backups",
    "mercury_logs",
    ".mercury_control",
    "mercury_manifests",
    "mercury_runbooks",
    "mercury_restore_checks",
    "mercury_state",
)
HISTORICAL_PHASE3B_MERCURY_COMMIT = "40b8f532ff2b49e9cdd699d4af01e88dde9aa8c0"


@dataclass(frozen=True)
class RetentionPolicy:
    """Fail-closed retention and destination packaging policy."""

    protected_run_ids: tuple[str, ...] = DEFAULT_PROTECTED_RUN_IDS
    protected_backup_ids: tuple[str, ...] = DEFAULT_PROTECTED_BACKUP_IDS
    protected_capture_ids: tuple[str, ...] = DEFAULT_PROTECTED_CAPTURE_IDS
    historical_phase3b_mercury_commit: str = HISTORICAL_PHASE3B_MERCURY_COMMIT
    current_destination_mercury_commit: str = ""
    current_destination_mercury_capture_id: str = ""
    historical_erebus_capture_ids: tuple[str, ...] = DEFAULT_PROTECTED_CAPTURE_IDS
    current_erebus_destination_commit: str = ""
    manual_review_roots: tuple[str, ...] = DEFAULT_MANUAL_REVIEW_ROOTS
    exclude_from_destination_by_default: tuple[str, ...] = DEFAULT_EXCLUDE_DESTINATION
    allow_scytaledroid_in_destination: bool = False
    scytaledroid_approved_paths: tuple[str, ...] = ()
    destination_validation_pending: bool = True
    allow_execute: bool = False
    quarantine_only: bool = True
    safe_candidate_estimate_gib: float = 6.5
    manual_review_project_estimate_gib: float = 266.0
    governed_roots: tuple[str, ...] = DEFAULT_GOVERNED_ROOTS
    development_keep_latest_verified: int = 2
    development_include_in_destination: bool = False
    source_path: Path | None = None

    def cleanup_execute_allowed(self) -> bool:
        """Execute (even quarantine) requires validation complete and explicit allow."""
        return (
            not self.destination_validation_pending
            and self.allow_execute
            and self.quarantine_only
        )

    def is_manual_review_root(self, name: str) -> bool:
        return name.strip() in set(self.manual_review_roots)

    def is_scytaledroid_root(self, name: str) -> bool:
        return name.strip() in set(DEFAULT_MANUAL_REVIEW_ROOTS)

    def is_excluded_from_destination(self, name: str) -> bool:
        return name.strip() in set(self.exclude_from_destination_by_default)

    def is_governed_root(self, name: str) -> bool:
        return name.strip() in set(self.governed_roots)

    def protects_backup_id(self, backup_id: str) -> bool:
        return (backup_id or "").strip() in set(self.protected_backup_ids)

    def protects_run_id(self, run_id: str) -> bool:
        return (run_id or "").strip() in set(self.protected_run_ids)

    def protects_capture_id(self, capture_id: str) -> bool:
        return (capture_id or "").strip() in set(self.protected_capture_ids)


def _as_str_tuple(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return default


def _load_toml(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_retention_policy(*, config_path: Path | None = None) -> RetentionPolicy:
    """Load retention.toml when present, else retention.example.toml, else defaults."""
    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(config_path)
    else:
        candidates.extend([RETENTION_LOCAL, RETENTION_EXAMPLE])
    path: Path | None = None
    data: dict[str, Any] = {}
    for candidate in candidates:
        if candidate.is_file():
            path = candidate
            data = _load_toml(candidate)
            break

    protection = data.get("protection") if isinstance(data.get("protection"), dict) else {}
    exclusions = data.get("exclusions") if isinstance(data.get("exclusions"), dict) else {}
    cleanup = data.get("cleanup") if isinstance(data.get("cleanup"), dict) else {}
    governed = (
        cleanup.get("governed_roots")
        if isinstance(cleanup.get("governed_roots"), dict)
        else {}
    )
    development = (
        data.get("development_backups")
        if isinstance(data.get("development_backups"), dict)
        else {}
    )

    return RetentionPolicy(
        protected_run_ids=_as_str_tuple(
            protection.get("protected_run_ids"), default=DEFAULT_PROTECTED_RUN_IDS
        ),
        protected_backup_ids=_as_str_tuple(
            protection.get("protected_backup_ids"), default=DEFAULT_PROTECTED_BACKUP_IDS
        ),
        protected_capture_ids=_as_str_tuple(
            protection.get("protected_capture_ids"), default=DEFAULT_PROTECTED_CAPTURE_IDS
        ),
        historical_phase3b_mercury_commit=str(
            protection.get("historical_phase3b_mercury_commit")
            or HISTORICAL_PHASE3B_MERCURY_COMMIT
        ).strip(),
        current_destination_mercury_commit=str(
            protection.get("current_destination_mercury_commit") or ""
        ).strip(),
        current_destination_mercury_capture_id=str(
            protection.get("current_destination_mercury_capture_id") or ""
        ).strip(),
        historical_erebus_capture_ids=_as_str_tuple(
            protection.get("historical_erebus_capture_ids"),
            default=DEFAULT_PROTECTED_CAPTURE_IDS,
        ),
        current_erebus_destination_commit=str(
            protection.get("current_erebus_destination_commit") or ""
        ).strip(),
        manual_review_roots=_as_str_tuple(
            exclusions.get("manual_review_roots"), default=DEFAULT_MANUAL_REVIEW_ROOTS
        ),
        exclude_from_destination_by_default=_as_str_tuple(
            exclusions.get("exclude_from_destination_by_default"),
            default=DEFAULT_EXCLUDE_DESTINATION,
        ),
        allow_scytaledroid_in_destination=bool(
            exclusions.get("allow_scytaledroid_in_destination", False)
        ),
        scytaledroid_approved_paths=_as_str_tuple(
            exclusions.get("scytaledroid_approved_paths"), default=()
        ),
        destination_validation_pending=bool(
            cleanup.get("destination_validation_pending", True)
        ),
        allow_execute=bool(cleanup.get("allow_execute", False)),
        quarantine_only=bool(cleanup.get("quarantine_only", True)),
        safe_candidate_estimate_gib=float(
            cleanup.get("safe_candidate_estimate_gib", 6.5)
        ),
        manual_review_project_estimate_gib=float(
            cleanup.get("manual_review_project_estimate_gib", 266.0)
        ),
        governed_roots=_as_str_tuple(
            governed.get("names"), default=DEFAULT_GOVERNED_ROOTS
        ),
        development_keep_latest_verified=int(
            development.get("keep_latest_verified", 2) or 2
        ),
        development_include_in_destination=bool(
            development.get("include_in_destination_handoff", False)
        ),
        source_path=path,
    )
