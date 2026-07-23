"""Backup and Sync session models (Phase 2 orchestration)."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SessionResult(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    REFUSED = "REFUSED"
    CANCELLED = "CANCELLED"
    NOT_RUN = "NOT_RUN"


class LaneResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    REFUSED = "REFUSED"
    NOT_RUN = "NOT_RUN"


PHASE3B_SEPARATION_NOTE = (
    "Routine backup-and-sync session. "
    "Does not supersede Phase 3B (20260722T055400Z_phase3b). "
    "Does not update the verified destination package. "
    "Requires explicit restore-check and package promotion."
)


class SessionPlan(BaseModel):
    """Operator-selected lanes for one Backup and Sync session."""

    production_backup: bool = True
    verify_production: bool = True
    development_backup: bool = False
    verify_development: bool = True
    git_recovery: bool = True
    sync_development: bool = False
    restore_check: bool = False
    # When True, selected restore-check failure is FAIL (promoted). Default optional → PARTIAL.
    restore_check_required: bool = False
    # Git is required when selected in the recommended session; custom plans may deselect it.
    git_recovery_required: bool = True

    def normalize(self) -> SessionPlan:
        """Enforce safety invariants on the selected plan."""
        data = self.model_copy(deep=True)
        if data.production_backup:
            data.verify_production = True
        if data.development_backup:
            data.verify_development = True
        if not data.production_backup and data.sync_development:
            data.sync_development = False
        if not data.git_recovery:
            data.git_recovery_required = False
        if not data.restore_check:
            data.restore_check_required = False
        return data


class FrozenSessionPlan(BaseModel):
    """Immutable execution plan sealed after customization and before lanes run."""

    plan_id: str
    plan_digest: str = ""
    selected_lanes: list[str] = Field(default_factory=list)
    required_lanes: list[str] = Field(default_factory=list)
    production_backup: bool = True
    verify_production: bool = True
    development_backup: bool = False
    verify_development: bool = True
    git_recovery: bool = True
    git_recovery_required: bool = True
    sync_development: bool = False
    restore_check: bool = False
    restore_check_required: bool = False
    storage_transition_requirement: str = ""
    confirmation_requirements: list[str] = Field(default_factory=list)
    database_set: list[str] = Field(default_factory=list)
    repository_set: list[str] = Field(default_factory=list)
    source_plan: SessionPlan = Field(default_factory=SessionPlan)


def freeze_session_plan(
    plan: SessionPlan,
    *,
    plan_id: str | None = None,
    storage_transition_requirement: str = "",
    confirmation_requirements: list[str] | None = None,
    database_set: list[str] | None = None,
    repository_set: list[str] | None = None,
) -> FrozenSessionPlan:
    """Seal a normalized plan for execution (no further menu defaults applied)."""
    from datetime import datetime, timezone

    selected = plan.normalize()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pid = plan_id or f"{stamp}_plan"
    selected_lanes: list[str] = []
    required_lanes: list[str] = []
    if selected.production_backup:
        selected_lanes.append("production_backup")
        required_lanes.append("production_backup")
    if selected.verify_production:
        selected_lanes.append("verify_production")
        required_lanes.append("verify_production")
    if selected.development_backup:
        selected_lanes.append("development_backup")
    if selected.verify_development and selected.development_backup:
        selected_lanes.append("verify_development")
    if selected.git_recovery:
        selected_lanes.append("git_recovery")
        if selected.git_recovery_required:
            required_lanes.append("git_recovery")
    if selected.sync_development:
        selected_lanes.append("sync_development")
    if selected.restore_check:
        selected_lanes.append("restore_check")
        if selected.restore_check_required:
            required_lanes.append("restore_check")

    frozen = FrozenSessionPlan(
        plan_id=pid,
        selected_lanes=selected_lanes,
        required_lanes=required_lanes,
        production_backup=selected.production_backup,
        verify_production=selected.verify_production,
        development_backup=selected.development_backup,
        verify_development=selected.verify_development,
        git_recovery=selected.git_recovery,
        git_recovery_required=selected.git_recovery_required,
        sync_development=selected.sync_development,
        restore_check=selected.restore_check,
        restore_check_required=selected.restore_check_required,
        storage_transition_requirement=storage_transition_requirement,
        confirmation_requirements=list(confirmation_requirements or []),
        database_set=list(database_set or []),
        repository_set=list(repository_set or []),
        source_plan=selected,
    )
    digest_payload = frozen.model_dump(mode="json")
    digest_payload.pop("plan_digest", None)
    frozen.plan_digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return frozen


class DatabaseArtifactRecord(BaseModel):
    database: str
    backup_id: str = ""
    artifact_path: str = ""
    artifact_sha256: str = ""
    manifest_stamp: str = ""
    manifest_verification: str = "NOT_RUN"
    artifact_verification: str = "NOT_RUN"
    restore_check_status: str = "NOT_RUN"
    verification_result: str = "NOT_RUN"  # compat alias of artifact_verification
    lane: str = "production"


class GitArtifactRecord(BaseModel):
    repository: str
    commit: str = ""
    branch: str = ""
    dirty_state: bool = False
    capture_id: str = ""
    bundle_path: str = ""
    bundle_sha256: str = ""
    verification: str = "NOT_RUN"
    # Compat aliases
    capture_path: str = ""
    verification_result: str = "NOT_RUN"
    dirty: bool = False
    error: str = ""


class SyncArtifactRecord(BaseModel):
    source_schema: str
    destination_schema: str
    sync_event_id: str = ""
    sync_run_id: str = ""  # compat alias of sync_event_id
    mode: str = "preview"  # preview | execute
    verification: str = "NOT_RUN"
    verification_result: str = "NOT_RUN"
    backup_id: str = ""
    message: str = ""


class RestoreCheckArtifactRecord(BaseModel):
    backup_id: str
    database: str = ""
    restore_schema: str = ""
    started_at: str = ""
    finished_at: str = ""
    result: str = "NOT_RUN"
    message: str = ""


class LaneSummary(BaseModel):
    requested: bool = False
    required: bool = False
    attempted: bool = False
    result: LaneResult = LaneResult.NOT_RUN
    selected: int = 0
    written: int = 0
    verified: int = 0
    failed: int = 0
    message: str = ""


class StorageTransitionRecord(BaseModel):
    required: bool = False
    classification: str = ""
    transition_id: str = ""
    transition_status: str = ""
    confirmation_class: str = ""
    package_id: str = ""
    source_delta: dict[str, Any] = Field(default_factory=dict)


class BackupSyncSession(BaseModel):
    session_id: str
    started_at: str = ""
    finished_at: str = ""
    host_identity: str = ""
    operator_intent: str = "backup_and_sync"
    requested_operations: SessionPlan = Field(default_factory=SessionPlan)
    frozen_plan: FrozenSessionPlan | None = None
    storage_preflight: dict[str, Any] = Field(default_factory=dict)
    storage_transition: StorageTransitionRecord = Field(
        default_factory=StorageTransitionRecord
    )
    production_backup_result: LaneSummary = Field(default_factory=LaneSummary)
    development_backup_result: LaneSummary = Field(default_factory=LaneSummary)
    git_capture_result: LaneSummary = Field(default_factory=LaneSummary)
    production_dev_sync_result: LaneSummary = Field(default_factory=LaneSummary)
    verification_result: LaneSummary = Field(default_factory=LaneSummary)
    restore_check_result: LaneSummary = Field(default_factory=LaneSummary)
    artifacts_result: str = "NOT_RUN"
    session_result: SessionResult = SessionResult.NOT_RUN
    warnings: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    exact_artifact_ids: list[str] = Field(default_factory=list)
    database_artifacts: list[DatabaseArtifactRecord] = Field(default_factory=list)
    git_artifacts: list[GitArtifactRecord] = Field(default_factory=list)
    sync_artifacts: list[SyncArtifactRecord] = Field(default_factory=list)
    restore_check_artifacts: list[RestoreCheckArtifactRecord] = Field(
        default_factory=list
    )
    recommended_next_action: str = ""
    receipt_path: str = ""
    receipt_sha256: str = ""
    receipt_result: str = "NOT_WRITTEN"
    phase3b_separation_note: str = PHASE3B_SEPARATION_NOTE
    preview: bool = False

    @property
    def overall_pass(self) -> bool:
        return self.session_result == SessionResult.PASS


def new_session_id(*, now: str | None = None) -> str:
    from datetime import datetime, timezone

    stamp = now or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_backup_sync"


def recommended_session_plan() -> SessionPlan:
    return SessionPlan(
        production_backup=True,
        verify_production=True,
        development_backup=False,
        verify_development=True,
        git_recovery=True,
        git_recovery_required=True,
        sync_development=False,
        restore_check=False,
        restore_check_required=False,
    ).normalize()


def classify_session_result(session: BackupSyncSession) -> SessionResult:
    """Derive overall session result from lane outcomes and receipt status."""
    if session.session_result in {SessionResult.REFUSED, SessionResult.CANCELLED}:
        return session.session_result

    plan = session.requested_operations
    frozen = session.frozen_plan
    required = set(frozen.required_lanes if frozen is not None else [])

    def _lane_required(name: str, fallback: bool) -> bool:
        if frozen is not None:
            return name in required
        return fallback

    required_fail = False
    any_optional_fail = False

    prod = session.production_backup_result
    if plan.production_backup:
        if prod.result in {LaneResult.FAIL, LaneResult.REFUSED}:
            if _lane_required("production_backup", True):
                required_fail = True
            else:
                any_optional_fail = True
        elif prod.result == LaneResult.PARTIAL:
            any_optional_fail = True

    verify = session.verification_result
    if plan.verify_production and plan.production_backup:
        if verify.result == LaneResult.FAIL:
            if _lane_required("verify_production", True):
                required_fail = True
            else:
                any_optional_fail = True

    git = session.git_capture_result
    if plan.git_recovery:
        if git.result in {LaneResult.FAIL, LaneResult.REFUSED}:
            if _lane_required("git_recovery", plan.git_recovery_required):
                required_fail = True
            else:
                any_optional_fail = True
        elif git.result == LaneResult.PARTIAL:
            any_optional_fail = True

    if plan.development_backup:
        if session.development_backup_result.result in {
            LaneResult.FAIL,
            LaneResult.PARTIAL,
        }:
            any_optional_fail = True

    if plan.sync_development:
        if session.production_dev_sync_result.result in {
            LaneResult.FAIL,
            LaneResult.PARTIAL,
        }:
            any_optional_fail = True

    if plan.restore_check:
        rc = session.restore_check_result
        if rc.result in {LaneResult.FAIL, LaneResult.PARTIAL}:
            if _lane_required("restore_check", plan.restore_check_required):
                required_fail = True
            else:
                any_optional_fail = True

    if session.receipt_result == "FAILED":
        any_optional_fail = True

    if required_fail:
        return SessionResult.FAIL
    if any_optional_fail:
        return SessionResult.PARTIAL
    if session.receipt_result not in {"WRITTEN", "NOT_REQUIRED", "HOST_LOCAL_REFUSAL"}:
        if session.preview:
            return SessionResult.PASS
        return SessionResult.PARTIAL
    return SessionResult.PASS
