"""Storage cleanup status/preview — execute locked until destination validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
import json

from mercury.core.storage_roles import CONTROL_DIRNAME
from mercury.storage.retention import RetentionPolicy, load_retention_policy

class CleanupClassification(StrEnum):
    PROTECTED = "PROTECTED"
    RETAIN = "RETAIN"
    EXCLUDE_FROM_DESTINATION = "EXCLUDE_FROM_DESTINATION"
    CLEANUP_CANDIDATE_AFTER_DESTINATION = "CLEANUP_CANDIDATE_AFTER_DESTINATION"
    MANUAL_REVIEW_ONLY = "MANUAL_REVIEW_ONLY"
    UNKNOWN_REFUSE = "UNKNOWN_REFUSE"

SCYTALE_AUDIT_RECEIPT_NAME = "scytaledroid_audit_receipt.json"
DEFAULT_AUDIT_TIMESTAMP = "2026-07-22T17:40:52Z"


@dataclass(frozen=True)
class ProjectAuditReceipt:
    root_name: str
    audit_timestamp: str
    size_bytes: int
    file_count: int | None
    classification: str
    manual_review_reason: str
    deep_audit_reference: str
    auto_cleanup: str = "prohibited"
    destination_default: str = "excluded"


@dataclass
class CleanupStatusReport:
    protected_size_bytes: int = 0
    manual_review_size_bytes: int = 0
    routine_retained_size_bytes: int = 0
    safe_candidate_estimate_bytes: int = 0
    scytaledroid_excluded_size_bytes: int = 0
    last_audit_timestamp: str = DEFAULT_AUDIT_TIMESTAMP
    destination_validation_pending: bool = True
    cleanup_execute_allowed: bool = False
    cleanup_execution_state: str = "refused"
    governed_roots: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    scytale_receipts: list[ProjectAuditReceipt] = field(default_factory=list)


@dataclass(frozen=True)
class CleanupPreviewEntry:
    path: str
    classification: CleanupClassification
    size_bytes: int
    reason: str
    references: tuple[str, ...] = ()
    canonical_replacement: str | None = None
    risk: str = "low"


@dataclass
class CleanupPreviewReport:
    generated_at: str
    mount_root: str
    entries: list[CleanupPreviewEntry] = field(default_factory=list)
    execute_refused_reason: str = "destination_validation_pending"
    plan_written: str | None = None


def _tree_size_bytes(path: Path) -> int:
    """Bounded size walk for governed Mercury roots (never deep-scans Scytale APKs)."""
    import os

    total = 0
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).lstat().st_size
            except OSError:
                continue
    return total


def default_scytaledroid_receipts() -> list[ProjectAuditReceipt]:
    """Cached audit summary — avoids re-walking APK trees during normal ops."""
    reason = (
        "ownership=ScytaleDroid; retention_class=MANUAL_REVIEW_ONLY; "
        "auto_cleanup=prohibited; destination_default=excluded"
    )
    deep_ref = (
        "read-only audit 2026-07-22T17:40:52Z "
        "(cross-tree APK extras ~78.5 GiB informational only; not reclaimable by Mercury)"
    )
    # Approximate sizes from the accepted retention audit (not re-hashed).
    return [
        ProjectAuditReceipt(
            root_name="scytaledroid_migration_checkpoints",
            audit_timestamp=DEFAULT_AUDIT_TIMESTAMP,
            size_bytes=141_892_000_000,
            file_count=118_071,
            classification="MANUAL_REVIEW_ONLY",
            manual_review_reason=reason,
            deep_audit_reference=deep_ref,
        ),
        ProjectAuditReceipt(
            root_name="scytaledroid_apk_store_backups",
            audit_timestamp=DEFAULT_AUDIT_TIMESTAMP,
            size_bytes=84_950_000_000,
            file_count=47_316,
            classification="MANUAL_REVIEW_ONLY",
            manual_review_reason=reason,
            deep_audit_reference=deep_ref,
        ),
        ProjectAuditReceipt(
            root_name="scytaledroid_artifacts",
            audit_timestamp=DEFAULT_AUDIT_TIMESTAMP,
            size_bytes=58_990_000_000,
            file_count=1_632,
            classification="MANUAL_REVIEW_ONLY",
            manual_review_reason=reason,
            deep_audit_reference=deep_ref,
        ),
    ]


def load_or_default_scytaledroid_receipts(control_root: Path) -> list[ProjectAuditReceipt]:
    path = control_root / SCYTALE_AUDIT_RECEIPT_NAME
    if not path.is_file():
        return default_scytaledroid_receipts()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_scytaledroid_receipts()
    roots = data.get("roots") if isinstance(data, dict) else None
    if not isinstance(roots, list):
        return default_scytaledroid_receipts()
    out: list[ProjectAuditReceipt] = []
    for item in roots:
        if not isinstance(item, dict):
            continue
        out.append(
            ProjectAuditReceipt(
                root_name=str(item.get("root_name") or ""),
                audit_timestamp=str(item.get("audit_timestamp") or DEFAULT_AUDIT_TIMESTAMP),
                size_bytes=int(item.get("size_bytes") or 0),
                file_count=(
                    int(item["file_count"]) if item.get("file_count") is not None else None
                ),
                classification=str(item.get("classification") or "MANUAL_REVIEW_ONLY"),
                manual_review_reason=str(item.get("manual_review_reason") or ""),
                deep_audit_reference=str(item.get("deep_audit_reference") or ""),
                auto_cleanup=str(item.get("auto_cleanup") or "prohibited"),
                destination_default=str(item.get("destination_default") or "excluded"),
            )
        )
    return out or default_scytaledroid_receipts()


def collect_reference_tokens(mount_root: Path, policy: RetentionPolicy) -> set[str]:
    """Collect protected identity tokens from pins and lightweight text scans."""
    tokens: set[str] = set(policy.protected_run_ids)
    tokens.update(policy.protected_backup_ids)
    tokens.update(policy.protected_capture_ids)
    if policy.historical_phase3b_mercury_commit:
        tokens.add(policy.historical_phase3b_mercury_commit)
    tokens.update(policy.historical_erebus_capture_ids)

    control = mount_root / CONTROL_DIRNAME
    scan_roots = [
        control / "phase3b",
        control / "validation",
        mount_root / "mercury_manifests",
        mount_root / "mercury_runbooks",
        mount_root / "mercury_state",
        mount_root / "mercury_worktree_snapshots" / "20260722T055310Z",
        mount_root / "mercury_worktree_snapshots" / "20260722T055352Z",
    ]
    suffixes = {".json", ".jsonl", ".csv", ".md", ".sha256", ".txt"}
    for root in scan_roots:
        if not root.exists():
            continue
        import os

        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            # Bound walk depth for status/preview.
            rel = Path(dirpath).relative_to(root) if root.is_dir() else Path()
            if len(rel.parts) > 6:
                continue
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in suffixes and not name.endswith(".sha256"):
                    continue
                try:
                    if path.stat().st_size > 2_000_000:
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for token in policy.protected_backup_ids:
                    if token in text:
                        tokens.add(token)
                for token in policy.protected_run_ids:
                    if token in text:
                        tokens.add(token)
                if "20260722T055310Z" in text:
                    tokens.add("20260722T055310Z")
                if "20260722T055352Z" in text:
                    tokens.add("20260722T055352Z")
    return tokens


def classify_top_level(
    name: str,
    *,
    policy: RetentionPolicy,
) -> CleanupClassification:
    if policy.is_scytaledroid_root(name) or policy.is_manual_review_root(name):
        return CleanupClassification.MANUAL_REVIEW_ONLY
    if not policy.is_governed_root(name):
        # Project archives and non-Mercury trees are never auto-cleanup targets.
        return CleanupClassification.MANUAL_REVIEW_ONLY
    if name == CONTROL_DIRNAME:
        return CleanupClassification.PROTECTED
    if name in {"mercury_manifests", "mercury_runbooks", "mercury_state", "mercury_restore_checks"}:
        return CleanupClassification.PROTECTED
    if name in {"mercury_backups", "mercury_worktree_snapshots", "mercury_repo_backups", "mercury_logs"}:
        return CleanupClassification.RETAIN
    return CleanupClassification.UNKNOWN_REFUSE


def build_cleanup_status(
    mount_root: Path,
    *,
    policy: RetentionPolicy | None = None,
) -> CleanupStatusReport:
    policy = policy or load_retention_policy()
    control = mount_root / CONTROL_DIRNAME
    receipts = load_or_default_scytaledroid_receipts(control)
    scytale_size = sum(item.size_bytes for item in receipts)

    protected = 0
    routine = 0
    for name in policy.governed_roots:
        path = mount_root / name
        if not path.exists():
            continue
        size = _tree_size_bytes(path)
        classification = classify_top_level(name, policy=policy)
        if classification in {
            CleanupClassification.PROTECTED,
        }:
            protected += size
        else:
            routine += size

    # Phase3B control evidence is already inside .mercury_control; keep estimate.
    safe_estimate = int(policy.safe_candidate_estimate_gib * (1024**3))
    execute_allowed = policy.cleanup_execute_allowed()
    return CleanupStatusReport(
        protected_size_bytes=protected,
        manual_review_size_bytes=int(
            policy.manual_review_project_estimate_gib * (1024**3)
        ),
        routine_retained_size_bytes=routine,
        safe_candidate_estimate_bytes=safe_estimate,
        scytaledroid_excluded_size_bytes=scytale_size,
        last_audit_timestamp=receipts[0].audit_timestamp if receipts else DEFAULT_AUDIT_TIMESTAMP,
        destination_validation_pending=policy.destination_validation_pending,
        cleanup_execute_allowed=execute_allowed,
        cleanup_execution_state=(
            "quarantine_may_be_authorized" if execute_allowed else "refused"
        ),
        governed_roots=policy.governed_roots,
        notes=[
            "Cleanup execute is locked while destination_validation_pending=true.",
            "ScytaleDroid roots are never automatic cleanup candidates.",
            "78.5 GiB Scytale APK cross-tree extras are informational only.",
        ],
        scytale_receipts=receipts,
    )


def build_cleanup_preview(
    mount_root: Path,
    *,
    policy: RetentionPolicy | None = None,
    write_plan_path: Path | None = None,
) -> CleanupPreviewReport:
    policy = policy or load_retention_policy()
    tokens = collect_reference_tokens(mount_root, policy)
    entries: list[CleanupPreviewEntry] = []

    # Top-level classifications only for Scytale + non-governed (no deep APK walk).
    try:
        children = sorted(mount_root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        children = []
    for child in children:
        name = child.name
        if name == "lost+found":
            continue
        classification = classify_top_level(name, policy=policy)
        if policy.is_scytaledroid_root(name):
            # Force dual semantics required by policy.
            entries.append(
                CleanupPreviewEntry(
                    path=str(child),
                    classification=CleanupClassification.MANUAL_REVIEW_ONLY,
                    size_bytes=0,
                    reason="ScytaleDroid ownership — auto_cleanup prohibited",
                    risk="high",
                )
            )
            entries.append(
                CleanupPreviewEntry(
                    path=str(child),
                    classification=CleanupClassification.EXCLUDE_FROM_DESTINATION,
                    size_bytes=0,
                    reason="Excluded from destination package by default",
                    risk="low",
                )
            )
            continue
        if classification == CleanupClassification.MANUAL_REVIEW_ONLY:
            entries.append(
                CleanupPreviewEntry(
                    path=str(child),
                    classification=classification,
                    size_bytes=0,
                    reason="Outside Mercury-governed cleanup roots",
                    risk="high",
                )
            )
            continue
        if classification == CleanupClassification.EXCLUDE_FROM_DESTINATION:
            entries.append(
                CleanupPreviewEntry(
                    path=str(child),
                    classification=classification,
                    size_bytes=0,
                    reason="Default destination exclusion",
                    risk="low",
                )
            )

    # Governed Mercury candidates — only propose after destination validation.
    backups = mount_root / "mercury_backups"
    if backups.is_dir():
        for database in ("erebus_threat_intel_dev", "scytaledroid_core_dev", "android_permission_intel_dev"):
            # Count generations lightly via day dirs.
            gens = 0
            try:
                for day in backups.iterdir():
                    db_dir = day / database
                    if db_dir.is_dir():
                        gens += sum(1 for child in db_dir.iterdir() if child.is_dir() or (child / "manifest.json").is_file() or child.name.endswith(".sql.gz"))
                        # better: use find_backup_directories
            except OSError:
                pass
            from mercury.backup.find_latest_backup import find_backup_directories

            paths = find_backup_directories(backups, database)
            keep = max(1, policy.development_keep_latest_verified)
            if len(paths) > keep and policy.destination_validation_pending:
                for path in sorted(paths, key=lambda p: p.name)[:-keep]:
                    backup_id = ""
                    try:
                        payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
                        backup_id = str(payload.get("backup_id") or "")
                    except (OSError, json.JSONDecodeError):
                        backup_id = ""
                    if backup_id and backup_id in tokens:
                        entries.append(
                            CleanupPreviewEntry(
                                path=str(path),
                                classification=CleanupClassification.PROTECTED,
                                size_bytes=0,
                                reason="referenced_by_sealed_evidence",
                                references=(backup_id,),
                                risk="high",
                            )
                        )
                        continue
                    entries.append(
                        CleanupPreviewEntry(
                            path=str(path),
                            classification=CleanupClassification.CLEANUP_CANDIDATE_AFTER_DESTINATION,
                            size_bytes=0,
                            reason=(
                                "Excess development generation beyond keep_"
                                f"{keep}; execute refused until destination validation"
                            ),
                            canonical_replacement="keep newest verified generations",
                            risk="medium",
                        )
                    )

    # Always protect Phase 3B and historical captures.
    for run_id in policy.protected_run_ids:
        phase_root = mount_root / CONTROL_DIRNAME / "phase3b" / run_id
        if phase_root.exists():
            entries.append(
                CleanupPreviewEntry(
                    path=str(phase_root),
                    classification=CleanupClassification.PROTECTED,
                    size_bytes=0,
                    reason="protected_run_id",
                    references=(run_id,),
                    risk="critical",
                )
            )
    for capture_id in policy.protected_capture_ids:
        # Capture may live under validation/erebus/<id>
        candidate = mount_root / CONTROL_DIRNAME / "validation" / "erebus" / capture_id
        if candidate.exists():
            entries.append(
                CleanupPreviewEntry(
                    path=str(candidate),
                    classification=CleanupClassification.PROTECTED,
                    size_bytes=0,
                    reason="protected_capture_id",
                    references=(capture_id,),
                    risk="critical",
                )
            )
    for stamp in ("20260722T055310Z", "20260722T055352Z"):
        path = mount_root / "mercury_worktree_snapshots" / stamp
        if path.exists():
            entries.append(
                CleanupPreviewEntry(
                    path=str(path),
                    classification=CleanupClassification.PROTECTED,
                    size_bytes=0,
                    reason="historical_phase3b_worktree_capture",
                    references=(stamp, policy.historical_phase3b_mercury_commit),
                    risk="critical",
                )
            )

    refuse = "destination_validation_pending"
    if not policy.destination_validation_pending and not policy.allow_execute:
        refuse = "allow_execute=false"
    if policy.cleanup_execute_allowed():
        refuse = "execute still quarantine-only; use future execute command after review"

    report = CleanupPreviewReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        mount_root=str(mount_root),
        entries=entries,
        execute_refused_reason=refuse
        if not policy.cleanup_execute_allowed()
        else "preview_only_this_phase",
    )

    if write_plan_path is not None:
        write_plan_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": report.generated_at,
            "mount_root": report.mount_root,
            "execute_refused_reason": report.execute_refused_reason,
            "entries": [
                {
                    "path": e.path,
                    "classification": e.classification.value,
                    "size_bytes": e.size_bytes,
                    "reason": e.reason,
                    "references": list(e.references),
                    "canonical_replacement": e.canonical_replacement,
                    "risk": e.risk,
                }
                for e in entries
            ],
        }
        write_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        report.plan_written = str(write_plan_path)
    return report


def refuse_cleanup_execute(policy: RetentionPolicy | None = None) -> str:
    policy = policy or load_retention_policy()
    if policy.destination_validation_pending:
        return "cleanup execution refused: destination_validation_pending=true"
    if not policy.allow_execute:
        return "cleanup execution refused: allow_execute=false"
    return "cleanup execution refused: execute path not enabled in this Mercury phase"
