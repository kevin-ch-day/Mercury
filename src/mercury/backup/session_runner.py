"""Orchestrate a guided Backup and Sync session (Phase 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mercury.backup.session_models import (
    BackupSyncSession,
    DatabaseArtifactRecord,
    FrozenSessionPlan,
    GitArtifactRecord,
    LaneResult,
    LaneSummary,
    RestoreCheckArtifactRecord,
    SessionPlan,
    SessionResult,
    StorageTransitionRecord,
    SyncArtifactRecord,
    classify_session_result,
    freeze_session_plan,
    new_session_id,
    recommended_session_plan,
)
from mercury.storage.host_maintenance import (
    HostMaintenanceState,
    load_host_maintenance,
    mark_development_state_changed_since_package,
    mark_recovery_artifact_after_package,
)
from mercury.storage.operation_availability import (
    AvailabilityClassification,
    OperationAvailability,
    assess_operation_availability,
    ensure_backup_writes_available,
)
from mercury.storage.transitions import TransitionStatus


BackupBatchFn = Callable[..., Any]
VerifyBatchFn = Callable[..., Any]
GitCaptureFn = Callable[..., Any]
SyncBatchFn = Callable[..., Any]
RestoreCheckFn = Callable[..., Any]
EnsureWritesFn = Callable[..., OperationAvailability]


@dataclass
class SessionHooks:
    """Injectable lane runners for tests and non-interactive CLI."""

    ensure_writes: EnsureWritesFn | None = None
    run_production_backup: BackupBatchFn | None = None
    run_development_backup: BackupBatchFn | None = None
    verify_batch: VerifyBatchFn | None = None
    run_git_capture: GitCaptureFn | None = None
    run_sync: SyncBatchFn | None = None
    run_restore_check: RestoreCheckFn | None = None
    write_receipt: Callable[[BackupSyncSession], Path] | None = None
    write_host_refusal: Callable[[BackupSyncSession], Path] | None = None
    mark_recovery_artifact: Callable[..., HostMaintenanceState] | None = None
    mark_development_change: Callable[..., HostMaintenanceState] | None = None
    # Backward-compatible alias used by existing tests.
    mark_source_delta: Callable[..., HostMaintenanceState] | None = None
    resolve_fn: Callable[..., Any] | None = None
    ask_yes_no: Callable[[str, bool], bool | None] | None = None
    ask_phrase: Callable[[str], str] | None = None
    write: Callable[[str], None] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _host_line(host: HostMaintenanceState) -> str:
    return (
        f"writes_allowed={host.writes_allowed} "
        f"availability={host.storage_availability} "
        f"role={host.active_write_role}"
    )


def _artifacts_from_batch(
    batch: Any, *, lane: str, verification: Any | None = None
) -> list[DatabaseArtifactRecord]:
    records: list[DatabaseArtifactRecord] = []
    verified_ids = set(getattr(verification, "backup_ids", None) or [])
    for item in getattr(batch, "results", []) or []:
        manifest = getattr(item, "manifest", None)
        backup_id = ""
        sha = ""
        stamp = ""
        if manifest is not None:
            backup_id = str(getattr(manifest, "backup_id", "") or "")
            sha = str(getattr(manifest, "sha256", "") or "")
            stamp = str(getattr(manifest, "created_at", "") or "")
        path = str(
            getattr(item, "backup_directory_path", None)
            or getattr(item, "backup_directory", "")
            or ""
        )
        verify_result = "NOT_RUN"
        if verification is not None:
            if backup_id and backup_id in verified_ids:
                verify_result = "PASS"
            elif getattr(item, "executed", False):
                verify_result = "FAIL" if getattr(verification, "failed", 0) else "PASS"
        records.append(
            DatabaseArtifactRecord(
                database=str(getattr(item, "database", "")),
                backup_id=backup_id,
                artifact_path=path,
                artifact_sha256=sha,
                manifest_stamp=stamp,
                manifest_verification=verify_result if verification is not None else "NOT_RUN",
                artifact_verification=verify_result,
                restore_check_status="NOT_RUN",
                verification_result=verify_result,
                lane=lane,
            )
        )
    return records


def _lane_from_batch(
    batch: Any,
    *,
    requested: bool,
    required: bool = False,
    verification: Any | None = None,
) -> LaneSummary:
    if not requested:
        return LaneSummary(requested=False, required=False, attempted=False, result=LaneResult.SKIPPED)
    if batch is None:
        return LaneSummary(
            requested=True,
            required=required,
            attempted=False,
            result=LaneResult.FAIL,
            message="lane not executed",
        )
    results = list(getattr(batch, "results", []) or [])
    written = int(getattr(batch, "executed_count", 0) or 0)
    failed = sum(
        1
        for item in results
        if getattr(item, "refused", False) or getattr(item, "error", None)
    )
    verified = int(getattr(verification, "verified", 0) or 0) if verification else 0
    verify_failed = int(getattr(verification, "failed", 0) or 0) if verification else 0
    if written == 0 and failed:
        result = LaneResult.FAIL
    elif verify_failed or (verification is not None and verified < written):
        result = LaneResult.FAIL if written and verified == 0 else LaneResult.PARTIAL
    elif written == 0:
        result = LaneResult.FAIL
    else:
        result = LaneResult.PASS
    return LaneSummary(
        requested=True,
        required=required,
        attempted=True,
        result=result,
        selected=len(results) or written,
        written=written,
        verified=verified if verification is not None else 0,
        failed=failed + verify_failed,
    )


def preview_session(
    plan: SessionPlan | None = None,
    *,
    host: HostMaintenanceState | None = None,
    resolve_fn: Callable[..., Any] | None = None,
) -> BackupSyncSession:
    """Build a non-mutating session preview (no writes, no transitions)."""
    state = host or load_host_maintenance()
    selected = (plan or recommended_session_plan()).normalize()
    availability = assess_operation_availability(
        "database_backup", host=state, resolve_fn=resolve_fn
    )
    session = BackupSyncSession(
        session_id=new_session_id(),
        started_at=_utc_now(),
        finished_at=_utc_now(),
        host_identity=_host_line(state),
        requested_operations=selected,
        storage_preflight={
            "classification": availability.classification.value,
            "available": availability.available,
            "blockers": list(availability.blockers),
            "next_action": availability.next_action,
        },
        preview=True,
        session_result=SessionResult.PASS
        if availability.classification
        != AvailabilityClassification.HARD_BLOCK
        else SessionResult.REFUSED,
        receipt_result="NOT_REQUIRED",
        recommended_next_action=(
            "Restore source writer, then run Backup and Sync"
            if not availability.available
            else "Run Backup and Sync session"
        ),
    )
    if availability.is_hard_block:
        session.failures.extend(availability.blockers)
    return session


def run_backup_sync_session(
    plan: SessionPlan | None = None,
    *,
    execute: bool = True,
    preview: bool = False,
    interactive: bool = True,
    hooks: SessionHooks | None = None,
    host: HostMaintenanceState | None = None,
    confirm_restore_phrase: str | None = None,
    accept_recoverable: bool | None = None,
    production_sources: list[str] | None = None,
) -> BackupSyncSession:
    """Run or preview a Backup and Sync session.

    When ``preview=True`` or ``execute=False``, no lane runners are invoked and
    no HDD receipt is written.
    """
    hooks = hooks or SessionHooks()
    state = host or load_host_maintenance()
    selected = (plan or recommended_session_plan()).normalize()
    availability_preview = assess_operation_availability(
        "database_backup", host=state, resolve_fn=hooks.resolve_fn
    )
    confirm_reqs: list[str] = []
    if availability_preview.is_strong:
        confirm_reqs.append("RESTORE SOURCE WRITER")
    elif availability_preview.is_recoverable:
        confirm_reqs.append("recoverable_y_n")
    frozen = freeze_session_plan(
        selected,
        storage_transition_requirement=availability_preview.classification.value,
        confirmation_requirements=confirm_reqs,
        database_set=list(production_sources or []),
        repository_set=[],
    )
    # Execution uses only the frozen plan — never re-read menu defaults.
    selected = frozen.source_plan
    session = BackupSyncSession(
        session_id=new_session_id(),
        started_at=_utc_now(),
        host_identity=_host_line(state),
        requested_operations=selected,
        frozen_plan=frozen,
        preview=bool(preview or not execute),
    )

    if session.preview:
        previewed = preview_session(selected, host=state, resolve_fn=hooks.resolve_fn)
        previewed.session_id = session.session_id
        previewed.frozen_plan = frozen
        return previewed

    # --- 1–2. Storage preflight + transition ---
    ensure = hooks.ensure_writes or ensure_backup_writes_available
    availability = assess_operation_availability(
        "database_backup", host=state, resolve_fn=hooks.resolve_fn
    )
    session.storage_preflight = {
        "classification": availability.classification.value,
        "available": availability.available,
        "blockers": list(availability.blockers),
        "next_action": availability.next_action,
    }

    if availability.is_hard_block:
        session.session_result = SessionResult.REFUSED
        session.failures.extend(availability.blockers)
        session.recommended_next_action = availability.next_action or (
            "Mercury HDD and Storage → Reconnect or change storage mode"
        )
        session.finished_at = _utc_now()
        _maybe_host_refusal(session, hooks)
        return session

    if not availability.available:
        # Guided restoration — do not ask lane customization questions here.
        if availability.is_strong and not interactive and confirm_restore_phrase is None:
            session.session_result = SessionResult.REFUSED
            session.failures.append(
                "Non-interactive session requires --confirm-restore "
                "'RESTORE SOURCE WRITER' when destination rehearsal is active"
            )
            session.finished_at = _utc_now()
            _maybe_host_refusal(session, hooks)
            return session
        if availability.is_recoverable and not interactive and accept_recoverable is None:
            session.session_result = SessionResult.REFUSED
            session.failures.append(
                "Non-interactive recoverable restore requires --accept-restore=true"
            )
            session.finished_at = _utc_now()
            _maybe_host_refusal(session, hooks)
            return session

        restored = ensure(
            interactive=interactive,
            ask_yes_no=(
                hooks.ask_yes_no
                if hooks.ask_yes_no is not None
                else (
                    (lambda _p, _d=False: bool(accept_recoverable))
                    if accept_recoverable is not None
                    else None
                )
            ),
            ask_phrase=(
                hooks.ask_phrase
                if hooks.ask_phrase is not None
                else (
                    (lambda _p="": confirm_restore_phrase or "")
                    if confirm_restore_phrase is not None
                    else None
                )
            ),
            write=hooks.write,
            host=state,
            resolve_fn=hooks.resolve_fn,
        )
        session.storage_transition = StorageTransitionRecord(
            required=True,
            classification=availability.classification.value,
            transition_id=restored.transition_id,
            transition_status=(
                restored.transition_status.value
                if restored.transition_status is not None
                else ""
            ),
            confirmation_class=availability.classification.value,
            package_id=load_host_maintenance().package_id,
            source_delta={
                "source_writes_resumed_after_package": load_host_maintenance().source_writes_resumed_after_package,
                "source_delta_relative_to_package_id": load_host_maintenance().source_delta_relative_to_package_id,
                "source_delta_reason": load_host_maintenance().source_delta_reason,
            },
        )
        if not restored.available:
            if restored.operation_status.value == "CANCELLED":
                session.session_result = SessionResult.CANCELLED
            else:
                session.session_result = SessionResult.REFUSED
            session.failures.extend(restored.blockers or ("writer restoration declined or failed",))
            session.finished_at = _utc_now()
            _maybe_host_refusal(session, hooks)
            return session
    else:
        session.storage_transition = StorageTransitionRecord(
            required=False,
            classification=AvailabilityClassification.AVAILABLE.value,
            transition_status=TransitionStatus.ALREADY_SATISFIED.value,
        )

    # --- 3–4. Production backup + exact-ID verify ---
    production_batch = None
    production_verification = None
    if selected.production_backup:
        run_prod = hooks.run_production_backup or _default_production_backup
        try:
            production_batch = run_prod(sources=production_sources)
            session.production_backup_result = _lane_from_batch(
                production_batch, requested=True, required=True
            )
            if selected.verify_production and production_batch is not None:
                verify = hooks.verify_batch or _default_verify
                production_verification = verify(
                    production_batch, allow_development_backup=False
                )
                session.verification_result = LaneSummary(
                    requested=True,
                    required=True,
                    attempted=True,
                    result=(
                        LaneResult.PASS
                        if getattr(production_verification, "failed", 0) == 0
                        and getattr(production_verification, "verified", 0) > 0
                        else LaneResult.FAIL
                    ),
                    selected=session.production_backup_result.written,
                    written=session.production_backup_result.written,
                    verified=int(getattr(production_verification, "verified", 0) or 0),
                    failed=int(getattr(production_verification, "failed", 0) or 0),
                )
                session.production_backup_result = _lane_from_batch(
                    production_batch,
                    requested=True,
                    required=True,
                    verification=production_verification,
                )
            session.database_artifacts.extend(
                _artifacts_from_batch(
                    production_batch,
                    lane="production",
                    verification=production_verification,
                )
            )
            if session.production_backup_result.written:
                _record_recovery_artifact(
                    hooks,
                    artifact_type="database_backup",
                    operation="production_database_backup",
                    artifact_id=next(
                        (
                            a.backup_id
                            for a in session.database_artifacts
                            if a.lane == "production" and a.backup_id
                        ),
                        session.session_id,
                    ),
                )
        except Exception as exc:  # noqa: BLE001 — lane isolation
            session.production_backup_result = LaneSummary(
                requested=True,
                required=True,
                attempted=True,
                result=LaneResult.FAIL,
                message=str(exc),
            )
            session.failures.append(f"production backup failed: {exc}")

    prod_ok = (
        not selected.production_backup
        or session.production_backup_result.result == LaneResult.PASS
    )

    # --- 5–6. Optional development backup ---
    if selected.development_backup:
        run_dev = hooks.run_development_backup or _default_development_backup
        try:
            development_batch = run_dev()
            development_verification = None
            if selected.verify_development and development_batch is not None:
                verify = hooks.verify_batch or _default_verify
                development_verification = verify(
                    development_batch, allow_development_backup=True
                )
            session.development_backup_result = _lane_from_batch(
                development_batch,
                requested=True,
                required=False,
                verification=development_verification,
            )
            session.database_artifacts.extend(
                _artifacts_from_batch(
                    development_batch,
                    lane="development",
                    verification=development_verification,
                )
            )
            if session.development_backup_result.written:
                _record_recovery_artifact(
                    hooks,
                    artifact_type="database_backup",
                    operation="development_database_backup",
                    artifact_id=next(
                        (
                            a.backup_id
                            for a in session.database_artifacts
                            if a.lane == "development" and a.backup_id
                        ),
                        session.session_id,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            session.development_backup_result = LaneSummary(
                requested=True,
                required=False,
                attempted=True,
                result=LaneResult.FAIL,
                message=str(exc),
            )
            session.warnings.append(f"development backup failed: {exc}")
    else:
        session.development_backup_result = LaneSummary(
            requested=False, required=False, attempted=False, result=LaneResult.SKIPPED
        )

    # --- 7. Git recovery capture ---
    if selected.git_recovery:
        run_git = hooks.run_git_capture or _default_git_capture
        try:
            git_plan = run_git()
            session.git_artifacts = _git_artifacts_from_plan(git_plan)
            captured = sum(
                1
                for item in session.git_artifacts
                if item.verification_result == "PASS"
            )
            failed = sum(
                1
                for item in session.git_artifacts
                if item.verification_result == "FAIL" or item.error
            )
            selected_count = len(session.git_artifacts)
            if failed and captured == 0:
                result = LaneResult.FAIL
            elif failed:
                result = LaneResult.PARTIAL
            elif captured == 0:
                result = LaneResult.FAIL
            else:
                result = LaneResult.PASS
            session.git_capture_result = LaneSummary(
                requested=True,
                required=bool(selected.git_recovery_required),
                attempted=True,
                result=result,
                selected=selected_count,
                written=captured,
                verified=captured,
                failed=failed,
            )
            if captured:
                _record_recovery_artifact(
                    hooks,
                    artifact_type="git_capture",
                    operation="git_recovery_capture",
                    artifact_id=next(
                        (
                            g.capture_id
                            for g in session.git_artifacts
                            if g.capture_id
                        ),
                        session.session_id,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            session.git_capture_result = LaneSummary(
                requested=True,
                required=bool(selected.git_recovery_required),
                attempted=True,
                result=LaneResult.FAIL,
                message=str(exc),
            )
            session.warnings.append(f"git capture failed: {exc}")
    else:
        session.git_capture_result = LaneSummary(
            requested=False, required=False, attempted=False, result=LaneResult.SKIPPED
        )

    # --- 8. Optional prod→dev sync (only after verified production backup) ---
    if selected.sync_development:
        if not prod_ok:
            session.production_dev_sync_result = LaneSummary(
                requested=True,
                required=False,
                attempted=False,
                result=LaneResult.SKIPPED,
                message="Skipped because production backup/verification did not PASS",
            )
            session.warnings.append(
                "production-to-development sync skipped after production failure"
            )
        else:
            run_sync = hooks.run_sync or _default_sync
            try:
                sync_batch = run_sync()
                session.sync_artifacts = _sync_artifacts_from_batch(sync_batch)
                executed = int(getattr(sync_batch, "executed_count", 0) or 0)
                refused = int(getattr(sync_batch, "refused_count", 0) or 0)
                if refused and executed == 0:
                    result = LaneResult.FAIL
                elif refused:
                    result = LaneResult.PARTIAL
                elif executed == 0:
                    result = LaneResult.FAIL
                else:
                    result = LaneResult.PASS
                session.production_dev_sync_result = LaneSummary(
                    requested=True,
                    required=False,
                    attempted=True,
                    result=result,
                    selected=len(session.sync_artifacts),
                    written=executed,
                    verified=executed,
                    failed=refused,
                )
                if executed:
                    _record_development_change(
                        hooks,
                        operation="prod_to_dev_sync",
                        event_id=next(
                            (
                                s.sync_event_id or s.sync_run_id
                                for s in session.sync_artifacts
                                if s.sync_event_id or s.sync_run_id
                            ),
                            session.session_id,
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                session.production_dev_sync_result = LaneSummary(
                    requested=True,
                    required=False,
                    attempted=True,
                    result=LaneResult.FAIL,
                    message=str(exc),
                )
                session.warnings.append(f"sync failed: {exc}")
    else:
        session.production_dev_sync_result = LaneSummary(
            requested=False,
            required=False,
            attempted=False,
            result=LaneResult.SKIPPED,
            message="Not run",
        )

    # --- 9. Optional exact-ID restore-check (only when selected) ---
    if selected.restore_check:
        exact_ids = [
            a.backup_id
            for a in session.database_artifacts
            if a.backup_id and a.artifact_verification == "PASS"
        ]
        if not exact_ids:
            session.restore_check_result = LaneSummary(
                requested=True,
                required=bool(selected.restore_check_required),
                attempted=False,
                result=LaneResult.FAIL,
                message="No verified session backup IDs available for restore-check",
            )
            session.failures.append(
                "restore-check requested but no exact verified backup IDs from this session"
            )
        else:
            run_rc = hooks.run_restore_check or _default_restore_check
            try:
                rc_results = run_rc(exact_backup_ids=exact_ids)
                artifacts = _restore_check_artifacts(rc_results, exact_ids)
                session.restore_check_artifacts = artifacts
                passed = sum(1 for a in artifacts if a.result == "PASS")
                failed = sum(1 for a in artifacts if a.result == "FAIL")
                # Stamp restore_check_status onto matching DB artifacts.
                by_id = {a.backup_id: a.result for a in artifacts}
                for db_art in session.database_artifacts:
                    if db_art.backup_id in by_id:
                        db_art.restore_check_status = by_id[db_art.backup_id]
                if failed and passed == 0:
                    result = LaneResult.FAIL
                elif failed:
                    result = LaneResult.PARTIAL
                elif passed == 0:
                    result = LaneResult.FAIL
                else:
                    result = LaneResult.PASS
                session.restore_check_result = LaneSummary(
                    requested=True,
                    required=bool(selected.restore_check_required),
                    attempted=True,
                    result=result,
                    selected=len(exact_ids),
                    written=passed,
                    verified=passed,
                    failed=failed,
                )
            except Exception as exc:  # noqa: BLE001
                session.restore_check_result = LaneSummary(
                    requested=True,
                    required=bool(selected.restore_check_required),
                    attempted=True,
                    result=LaneResult.FAIL,
                    message=str(exc),
                )
                session.warnings.append(f"restore-check failed: {exc}")
    else:
        session.restore_check_result = LaneSummary(
            requested=False, required=False, attempted=False, result=LaneResult.SKIPPED
        )

    # Collect exact IDs from this session only (never "latest").
    session.exact_artifact_ids = [
        *(a.backup_id for a in session.database_artifacts if a.backup_id),
        *(g.capture_id for g in session.git_artifacts if g.capture_id),
        *(
            (s.sync_event_id or s.sync_run_id)
            for s in session.sync_artifacts
            if s.sync_event_id or s.sync_run_id
        ),
        *(r.backup_id for r in session.restore_check_artifacts if r.backup_id),
    ]
    session.artifacts_result = (
        "PASS"
        if not session.failures
        and session.production_backup_result.result
        in {LaneResult.PASS, LaneResult.SKIPPED, LaneResult.NOT_RUN}
        and session.git_capture_result.result
        in {LaneResult.PASS, LaneResult.SKIPPED, LaneResult.NOT_RUN, LaneResult.PARTIAL}
        else "PARTIAL"
        if session.database_artifacts or session.git_artifacts
        else "FAIL"
        if session.failures
        else "PASS"
    )

    # --- 10. Receipt ---
    try:
        write_receipt = hooks.write_receipt
        if write_receipt is None:
            from mercury.backup.session_receipt import write_session_receipt

            write_receipt = write_session_receipt
        path = write_receipt(session)
        session.receipt_path = str(path)
        session.receipt_result = "WRITTEN"
        # Prefer sidecar digest when present.
        sidecar = Path(path).with_suffix(Path(path).suffix + ".sha256")
        if not sidecar.exists():
            sidecar = Path(str(path) + ".sha256")
        if sidecar.is_file():
            session.receipt_sha256 = sidecar.read_text(encoding="utf-8").split()[0]
    except Exception as exc:  # noqa: BLE001
        session.receipt_result = "FAILED"
        session.warnings.append(f"session receipt write failed: {exc}")

    session.session_result = classify_session_result(session)
    session.recommended_next_action = _recommend_next(session)
    session.finished_at = _utc_now()
    return session


def _maybe_host_refusal(session: BackupSyncSession, hooks: SessionHooks) -> None:
    writer = hooks.write_host_refusal
    if writer is None:
        from mercury.backup.session_receipt import write_host_local_session_refusal

        writer = write_host_local_session_refusal
    try:
        path = writer(session)
        session.receipt_path = str(path)
        session.receipt_result = "HOST_LOCAL_REFUSAL"
    except Exception as exc:  # noqa: BLE001
        session.warnings.append(f"host-local refusal record failed: {exc}")


def _record_recovery_artifact(
    hooks: SessionHooks,
    *,
    artifact_type: str,
    artifact_id: str,
    operation: str = "",
) -> None:
    if hooks.mark_source_delta is not None:
        hooks.mark_source_delta(operation=operation or artifact_type, artifact_id=artifact_id)
        return
    marker = hooks.mark_recovery_artifact or mark_recovery_artifact_after_package
    marker(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        operation=operation,
    )


def _record_development_change(
    hooks: SessionHooks, *, operation: str, event_id: str
) -> None:
    if hooks.mark_source_delta is not None:
        hooks.mark_source_delta(operation=operation, artifact_id=event_id)
        return
    marker = hooks.mark_development_change or mark_development_state_changed_since_package
    marker(operation=operation, event_id=event_id)


def _recommend_next(session: BackupSyncSession) -> str:
    if session.session_result in {SessionResult.PASS, SessionResult.PARTIAL}:
        return "Safely disconnect Mercury HDD"
    if session.session_result == SessionResult.FAIL:
        return "Review session failures, then retry Backup and Sync"
    if session.session_result == SessionResult.REFUSED:
        return session.storage_preflight.get("next_action") or (
            "Mercury HDD and Storage → Reconnect or change storage mode"
        )
    return "Return to Backup Operations"


def _default_production_backup(*, sources: list[str] | None = None) -> Any:
    from mercury.backup.batch_runner import run_backup_batch
    from mercury.backup.backup_runner import BACKUP_KIND_FULL
    from mercury.core.execution_policy import load_execution_policy
    from mercury.core.runtime import should_probe_database_status

    return run_backup_batch(
        BACKUP_KIND_FULL,
        execute=True,
        live=should_probe_database_status(),
        policy=load_execution_policy(),
        sources=sources,
    )


def _default_development_backup() -> Any:
    from mercury.backup.batch_runner import (
        resolve_development_backup_sources,
        run_backup_batch,
    )
    from mercury.backup.backup_runner import BACKUP_KIND_FULL
    from mercury.core.execution_policy import load_execution_policy
    from mercury.core.runtime import should_probe_database_status

    sources = resolve_development_backup_sources(live=should_probe_database_status())
    return run_backup_batch(
        BACKUP_KIND_FULL,
        execute=True,
        live=should_probe_database_status(),
        policy=load_execution_policy(),
        sources=sources,
        allow_development_backup=True,
    )


def _default_verify(batch: Any, *, allow_development_backup: bool = False) -> Any:
    from mercury.backup.batch_runner import verify_written_backup_batch

    return verify_written_backup_batch(
        batch, allow_development_backup=allow_development_backup
    )


def _default_git_capture() -> Any:
    from mercury.repo.bundle import build_repo_bundle_plan, execute_repo_bundle_plan
    from mercury.repo.config import load_repo_bundle_settings, load_repo_definitions
    from mercury.repo.status import inspect_repositories

    statuses = inspect_repositories(load_repo_definitions())
    settings = load_repo_bundle_settings()
    plan = build_repo_bundle_plan(statuses, settings)
    return execute_repo_bundle_plan(plan)


def _default_sync() -> Any:
    from mercury.core.execution_policy import load_execution_policy
    from mercury.sync.readiness import build_sync_readiness_report
    from mercury.sync.sync_runner import run_sync_batch

    report = build_sync_readiness_report(live=True)
    ready = [entry for entry in report.entries if entry.ready_for_sync_planning]
    return run_sync_batch(ready, execute=True, policy=load_execution_policy())


def _default_restore_check(*, exact_backup_ids: list[str]) -> Any:
    """Restore-check only the exact backup IDs created by this session.

    Never selects unqualified ``latest``. Each ID is planned with
    ``require_backup_id=True`` and executed into a disposable ``_restorecheck_*``
    schema. Production schemas are never targeted.
    """
    from mercury.restore.check_plan import build_restore_check_plan
    from mercury.restore.restore_runner import execute_restore_into_database

    results: list[RestoreCheckArtifactRecord] = []
    for backup_id in exact_backup_ids:
        started = _utc_now()
        # Mercury backup_id: <database>-<kind>-<stamp>
        database = backup_id
        for kind in ("-full-", "-schema-", "-data-"):
            if kind in backup_id:
                database = backup_id.split(kind, 1)[0]
                break
        try:
            plan = build_restore_check_plan(
                database,
                backup_id=backup_id,
                require_backup_id=True,
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                RestoreCheckArtifactRecord(
                    backup_id=backup_id,
                    database=database,
                    started_at=started,
                    finished_at=_utc_now(),
                    result="FAIL",
                    message=str(exc),
                )
            )
            continue
        resolved_id = str(getattr(plan, "backup_id", "") or backup_id)
        if resolved_id != backup_id:
            results.append(
                RestoreCheckArtifactRecord(
                    backup_id=backup_id,
                    database=database,
                    restore_schema=str(getattr(plan, "restore_target", "") or ""),
                    started_at=started,
                    finished_at=_utc_now(),
                    result="FAIL",
                    message=(
                        f"restore-check refused: planned id {resolved_id!r} "
                        f"does not match session id {backup_id!r}"
                    ),
                )
            )
            continue
        if getattr(plan, "blockers", None) or not getattr(plan, "allowed", False):
            results.append(
                RestoreCheckArtifactRecord(
                    backup_id=resolved_id,
                    database=str(getattr(plan, "source_prod", database) or database),
                    restore_schema=str(getattr(plan, "restore_target", "") or ""),
                    started_at=started,
                    finished_at=_utc_now(),
                    result="FAIL",
                    message="; ".join(getattr(plan, "blockers", []) or ["not allowed"]),
                )
            )
            continue
        dump = getattr(plan, "dump_file", None)
        target = getattr(plan, "restore_target", None)
        source = str(getattr(plan, "source_prod", database) or database)
        try:
            if not dump or not target:
                raise RuntimeError("restore-check plan missing dump_file or restore_target")
            execute_restore_into_database(
                dump_path=Path(dump),
                target_database=str(target),
                source_database=source,
                execute=True,
                cleanup_after_success=True,
            )
            results.append(
                RestoreCheckArtifactRecord(
                    backup_id=resolved_id,
                    database=source,
                    restore_schema=str(target or ""),
                    started_at=started,
                    finished_at=_utc_now(),
                    result="PASS",
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                RestoreCheckArtifactRecord(
                    backup_id=resolved_id,
                    database=source,
                    restore_schema=str(target or ""),
                    started_at=started,
                    finished_at=_utc_now(),
                    result="FAIL",
                    message=str(exc),
                )
            )
    return results


def _restore_check_artifacts(
    results: Any, exact_ids: list[str]
) -> list[RestoreCheckArtifactRecord]:
    if isinstance(results, list) and results and isinstance(
        results[0], RestoreCheckArtifactRecord
    ):
        return list(results)
    artifacts: list[RestoreCheckArtifactRecord] = []
    if isinstance(results, list):
        for item, backup_id in zip(results, exact_ids, strict=False):
            if isinstance(item, RestoreCheckArtifactRecord):
                artifacts.append(item)
                continue
            artifacts.append(
                RestoreCheckArtifactRecord(
                    backup_id=str(getattr(item, "backup_id", backup_id) or backup_id),
                    database=str(getattr(item, "database", "") or ""),
                    restore_schema=str(
                        getattr(item, "restore_schema", None)
                        or getattr(item, "target_database", "")
                        or ""
                    ),
                    started_at=str(getattr(item, "started_at", "") or ""),
                    finished_at=str(getattr(item, "finished_at", "") or ""),
                    result=str(getattr(item, "result", "PASS") or "PASS"),
                    message=str(getattr(item, "message", "") or ""),
                )
            )
        return artifacts
    for backup_id in exact_ids:
        artifacts.append(
            RestoreCheckArtifactRecord(backup_id=backup_id, result="FAIL", message="no result")
        )
    return artifacts


def _git_artifacts_from_plan(plan: Any) -> list[GitArtifactRecord]:
    records: list[GitArtifactRecord] = []
    for entry in getattr(plan, "entries", []) or []:
        path = str(getattr(entry, "planned_bundle_path", "") or "")
        capture_id = Path(path).name if path else ""
        sha = str(getattr(entry, "bundle_sha256", "") or "")
        verified = bool(getattr(entry, "bundle_verified", False)) and bool(
            getattr(entry, "executed", False)
        )
        error = str(getattr(entry, "error", "") or "")
        verify = "FAIL" if error or not verified else "PASS"
        dirty = bool(getattr(entry, "dirty", False))
        records.append(
            GitArtifactRecord(
                repository=str(
                    getattr(entry, "display_name", None)
                    or getattr(entry, "key", "")
                    or ""
                ),
                commit=str(getattr(entry, "commit", "") or ""),
                branch=str(getattr(entry, "branch", "") or ""),
                dirty_state=dirty,
                capture_id=capture_id,
                bundle_path=path,
                bundle_sha256=sha,
                verification=verify,
                capture_path=path,
                verification_result=verify,
                dirty=dirty,
                error=error,
            )
        )
    return records


def _sync_artifacts_from_batch(batch: Any) -> list[SyncArtifactRecord]:
    stamp = _stamp()
    records: list[SyncArtifactRecord] = []
    for index, item in enumerate(getattr(batch, "results", []) or [], start=1):
        executed = bool(getattr(item, "executed", False))
        refused = bool(getattr(item, "refused", False))
        verified = getattr(item, "verification_passed", None)
        if refused:
            verify_result = "REFUSED"
        elif verified is True:
            verify_result = "PASS"
        elif verified is False:
            verify_result = "FAIL"
        elif executed:
            verify_result = "PASS"
        else:
            verify_result = "NOT_RUN"
        event_id = f"{stamp}_sync_{index:02d}"
        records.append(
            SyncArtifactRecord(
                source_schema=str(getattr(item, "source", "")),
                destination_schema=str(getattr(item, "target", "")),
                sync_event_id=event_id,
                sync_run_id=event_id,
                mode="execute" if executed else "preview",
                verification=verify_result,
                verification_result=verify_result,
                backup_id="",
                message=str(getattr(item, "message", "") or ""),
            )
        )
    return records
