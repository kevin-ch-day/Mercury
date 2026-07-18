"""Workstation handoff readiness checklist."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.state.summary import build_state_summary
from mercury.transfer.bundle import (
    TransferBundle,
    build_transfer_bundle,
    database_package_status_for_bundle,
    handoff_status_for_bundle,
    repository_package_status_for_bundle,
)


class HandoffStep(BaseModel):
    step_key: str | None = None
    label: str
    status: str
    detail: str
    action: str | None = None


class HandoffChecklist(BaseModel):
    handoff_status: str
    database_package: str
    repository_package: str
    latest_transfer_manifest: str | None = None
    latest_transfer_age: str | None = None
    latest_database_bundle_manifest: str | None = None
    latest_database_bundle_age: str | None = None
    state_bundle_rows: int = 0
    steps: list[HandoffStep] = Field(default_factory=list)

    def recommended_actions(self) -> list[str]:
        """Distinct action hints from failed or warned checklist steps."""
        actions: list[str] = []
        seen: set[str] = set()
        for step in self.steps:
            if step.status not in {"fail", "warn"} or not step.action:
                continue
            if step.action in seen:
                continue
            seen.add(step.action)
            actions.append(step.action)
        return actions


def _path_mtime(path: Path | None) -> datetime | None:
    if path is None or not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _latest_backup_mtime(bundle: TransferBundle) -> datetime | None:
    latest: datetime | None = None
    for entry in bundle.database_entries:
        if not entry.backup_directory:
            continue
        backup_dir = Path(entry.backup_directory)
        if not backup_dir.is_dir():
            continue
        mtime = datetime.fromtimestamp(backup_dir.stat().st_mtime, tz=timezone.utc)
        if latest is None or mtime > latest:
            latest = mtime
    return latest


def _manifest_is_stale(manifest_path: Path | None, backup_mtime: datetime | None) -> bool:
    manifest_mtime = _path_mtime(manifest_path)
    if manifest_mtime is None or backup_mtime is None:
        return False
    return manifest_mtime < backup_mtime


def _artifact_age_label(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    from mercury.terminal.format import format_human_datetime

    return format_human_datetime(mtime.isoformat())


def _latest_glob_file(base: Path, pattern: str) -> Path | None:
    if not base.is_dir():
        return None
    matches = sorted(base.glob(pattern))
    return matches[-1] if matches else None


def _build_steps(bundle: TransferBundle, *, policy) -> list[HandoffStep]:
    db_package = database_package_status_for_bundle(bundle)
    repo_package = repository_package_status_for_bundle(bundle)
    backup_mtime = _latest_backup_mtime(bundle)
    settings_manifest_dir = Path(bundle.manifest_dir)
    db_index = _latest_glob_file(settings_manifest_dir, "**/database_transfer_manifest_*.json")
    transfer_manifest = (
        Path(bundle.latest_transfer_manifest_path)
        if bundle.latest_transfer_manifest_path
        else None
    )
    steps: list[HandoffStep] = []

    root_state = policy.backup_root_state()
    if root_state == "usb-mounted":
        steps.append(
            HandoffStep(
                step_key="usb_root",
                label="Operator backup root",
                status="ok",
                detail=str(policy.backup_root),
            )
        )
    else:
        steps.append(
            HandoffStep(
                step_key="usb_root",
                label="Operator backup root",
                status="fail",
                detail=root_state,
                action="./run.sh repair-usb",
            )
        )

    source_count = len(bundle.database_entries)
    if source_count and bundle.verified_source_count == source_count:
        steps.append(
            HandoffStep(
                step_key="backups_verified",
                label="Database backups verified",
                status="ok",
                detail=(
                    f"{bundle.verified_source_count} of {source_count} sources "
                    "artifact-verified on operator storage"
                ),
            )
        )
    else:
        steps.append(
            HandoffStep(
                step_key="backups_verified",
                label="Database backups verified",
                status="fail",
                detail=f"{bundle.verified_source_count} of {source_count or 0} verified",
                action="Handoff [4] backup, then [5] verify",
            )
        )

    if bundle.stale_source_count or bundle.unknown_freshness_source_count:
        parts: list[str] = []
        if bundle.stale_source_count:
            parts.append(f"{bundle.stale_source_count} stale")
        if bundle.unknown_freshness_source_count:
            parts.append(f"{bundle.unknown_freshness_source_count} unknown")
        steps.append(
            HandoffStep(
                step_key="backup_freshness",
                label="Backup freshness",
                status="warn",
                detail=", ".join(parts),
                action="Handoff [4] run backup for stale sources",
            )
        )
    elif source_count:
        steps.append(
            HandoffStep(
                step_key="backup_freshness",
                label="Backup freshness",
                status="ok",
                detail="All verified sources are fresh",
            )
        )

    if repo_package == "complete":
        steps.append(
            HandoffStep(
                step_key="repo_bundles",
                label="Repository bundles",
                status="ok",
                detail="Verified repo bundles on operator storage",
            )
        )
    elif repo_package == "complete with warnings":
        dirty = len(bundle.dirty_repo_names)
        steps.append(
            HandoffStep(
                step_key="repo_bundles",
                label="Repository bundles",
                status="warn",
                detail=f"Bundles present but {dirty} repo(s) were dirty at bundle time",
                action="./run.sh repo bundle --execute after committing changes",
            )
        )
    else:
        steps.append(
            HandoffStep(
                step_key="repo_bundles",
                label="Repository bundles",
                status="fail",
                detail="Missing or unverified repo bundles on operator storage",
                action="Handoff [6] write repository bundles",
            )
        )

    if db_index is not None:
        steps.append(
            HandoffStep(
                step_key="db_bundle_index",
                label="Database bundle index",
                status="ok" if db_package != "partial" else "warn",
                detail=str(db_index),
                action=None if db_package == "complete" else "Handoff [7] write DB bundle",
            )
        )
    else:
        steps.append(
            HandoffStep(
                step_key="db_bundle_index",
                label="Database bundle index",
                status="fail",
                detail="No database_transfer_manifest on operator storage",
                action="Handoff [7] write DB bundle and runbooks",
            )
        )

    stale_manifests: list[str] = []
    if _manifest_is_stale(db_index, backup_mtime):
        stale_manifests.append("database bundle index")
    if _manifest_is_stale(transfer_manifest, backup_mtime):
        stale_manifests.append("transfer manifest")
    if stale_manifests:
        steps.append(
            HandoffStep(
                step_key="manifest_freshness",
                label="Manifest freshness",
                status="warn",
                detail=f"Older than latest backups: {', '.join(stale_manifests)}",
                action="Handoff [2] guided wizard or rewrite manifests after backup",
            )
        )
    elif backup_mtime is not None and (db_index is not None or transfer_manifest is not None):
        steps.append(
            HandoffStep(
                step_key="manifest_freshness",
                label="Manifest freshness",
                status="ok",
                detail="Manifests are current relative to latest operator backups",
            )
        )

    handoff = handoff_status_for_bundle(bundle)
    if transfer_manifest and transfer_manifest.is_file() and handoff == "complete":
        steps.append(
            HandoffStep(
                step_key="transfer_package",
                label="Combined transfer package",
                status="ok",
                detail=str(transfer_manifest),
            )
        )
    elif transfer_manifest and transfer_manifest.is_file():
        steps.append(
            HandoffStep(
                step_key="transfer_package",
                label="Combined transfer package",
                status="warn",
                detail=f"On operator storage but snapshot is {handoff}",
                action="Handoff [8] write transfer package when ready",
            )
        )
    else:
        steps.append(
            HandoffStep(
                step_key="transfer_package",
                label="Combined transfer package",
                status="fail",
                detail="No transfer_manifest on operator storage",
                action="Handoff [8] write transfer package",
            )
        )

    return steps


def build_handoff_checklist_from_bundle(
    bundle: TransferBundle,
    *,
    state_bundle_rows: int | None = None,
) -> HandoffChecklist:
    """Build a checklist from an already-resolved transfer bundle."""
    policy = load_execution_policy()
    rows = state_bundle_rows
    if rows is None:
        rows = build_state_summary().database_bundle_rows
    manifest_dir = Path(bundle.manifest_dir)
    transfer_path = (
        Path(bundle.latest_transfer_manifest_path)
        if bundle.latest_transfer_manifest_path
        else None
    )
    db_index = _latest_glob_file(manifest_dir, "**/database_transfer_manifest_*.json")
    return HandoffChecklist(
        handoff_status=handoff_status_for_bundle(bundle),
        database_package=database_package_status_for_bundle(bundle),
        repository_package=repository_package_status_for_bundle(bundle),
        latest_transfer_manifest=str(transfer_path) if transfer_path else None,
        latest_transfer_age=_artifact_age_label(transfer_path),
        latest_database_bundle_manifest=str(db_index) if db_index else None,
        latest_database_bundle_age=_artifact_age_label(db_index),
        state_bundle_rows=rows,
        steps=_build_steps(bundle, policy=policy),
    )


def build_handoff_checklist(*, live: bool | None = None) -> HandoffChecklist:
    use_live = should_probe_database_status() if live is None else live
    bundle = build_transfer_bundle(live=use_live)
    return build_handoff_checklist_from_bundle(bundle)
