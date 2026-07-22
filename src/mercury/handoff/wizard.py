"""Guided workstation handoff wizard — orchestrates backup through transfer write."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury.backup.batch_runner import run_backup_batch, verify_written_backup_batch
from mercury.backup.bundle import (
    build_database_bundle_plan,
    bundle_package_status,
    write_database_bundle_plan,
)
from mercury.backup.freshness import FRESHNESS_STALE, FRESHNESS_UNKNOWN
from mercury.backup.status import build_backup_status_report
from mercury.backup.terminal.verify import run_verify_all_for_menu
from mercury.core.execution_policy import load_execution_policy
from mercury.core.handoff_status import handoff_write_ack_prompt, handoff_write_requires_force
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.handoff.checklist import build_handoff_checklist
from mercury.repo.bundle import build_repo_bundle_plan, execute_repo_bundle_plan
from mercury.transfer import build_transfer_bundle, write_transfer_bundle
from mercury.transfer.bundle import handoff_status_for_bundle, resolve_transfer_live
from mercury.backup.write_preflight import assess_backup_write_preflight


def _maintenance_block_phase(phase: str) -> HandoffWizardPhaseResult | None:
    """Refuse HDD-mutating handoff phases while detach maintenance is active."""
    preflight = assess_backup_write_preflight()
    if preflight.allowed:
        return None
    return HandoffWizardPhaseResult(
        phase=phase,
        status="failed",
        summary=(
            "Handoff write refused: Mercury HDD detach maintenance is active "
            f"({preflight.storage_availability}, writes_allowed=false)."
        ),
        detail=preflight.reason,
    )


class HandoffWizardPhaseResult(BaseModel):
    phase: str
    status: str
    summary: str
    detail: str | None = None


class HandoffWizardResult(BaseModel):
    phases: list[HandoffWizardPhaseResult] = Field(default_factory=list)
    final_handoff_status: str | None = None
    cancelled: bool = False


_WIZARD_PHASES: tuple[tuple[str, str], ...] = (
    ("backup", "Full backup for stale or missing sources"),
    ("verify", "Verify all source backups"),
    ("repo_bundle", "Write repository bundles"),
    ("db_bundle", "Write database bundle index and runbooks"),
    ("transfer", "Write combined transfer package"),
)

_PHASE_LABELS = {key: label for key, label in _WIZARD_PHASES}


def wizard_phase_choices() -> tuple[str, ...]:
    return tuple(key for key, _label in _WIZARD_PHASES)


def wizard_phase_label(phase: str) -> str:
    return _PHASE_LABELS.get(phase, phase.replace("_", " ").title())


def resolve_wizard_phase_range(
    *,
    start_phase: str | None = None,
    end_phase: str | None = None,
) -> list[str]:
    phases = list(wizard_phase_choices())
    if start_phase and start_phase not in phases:
        raise ValueError(
            f"Unknown start phase '{start_phase}'. Valid phases: {', '.join(phases)}."
        )
    if end_phase and end_phase not in phases:
        raise ValueError(
            f"Unknown end phase '{end_phase}'. Valid phases: {', '.join(phases)}."
        )
    start_index = phases.index(start_phase) if start_phase else 0
    end_index = phases.index(end_phase) if end_phase else len(phases) - 1
    if start_index > end_index:
        raise ValueError(
            f"Invalid phase range: start '{start_phase}' comes after end '{end_phase}'."
        )
    return phases[start_index : end_index + 1]


def sources_needing_backup(*, live: bool | None = None) -> list[str]:
    """Source databases that need a full backup before handoff."""
    use_live = should_probe_database_status() if live is None else live
    report = build_backup_status_report(live=use_live)
    names: list[str] = []
    for entry in report.entries:
        if entry.protection_status in {"missing", "failed", "untrusted root"}:
            names.append(entry.database)
            continue
        if entry.recommend_full_backup or entry.freshness in {FRESHNESS_STALE, FRESHNESS_UNKNOWN}:
            names.append(entry.database)
    return names


def run_handoff_backup_phase(*, live: bool | None = None, execute: bool = True) -> HandoffWizardPhaseResult:
    use_live = should_probe_database_status() if live is None else live
    sources = sources_needing_backup(live=use_live)
    if not sources:
        return HandoffWizardPhaseResult(
            phase="backup",
            status="skipped",
            summary="All sources already verified and fresh — backup not needed.",
        )
    if not execute:
        return HandoffWizardPhaseResult(
            phase="backup",
            status="skipped",
            summary=f"Would run full backup for {len(sources)} source(s).",
            detail=", ".join(sources),
        )
    blocked = _maintenance_block_phase("backup")
    if blocked is not None:
        return blocked
    policy = load_execution_policy()
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=True,
        live=use_live,
        policy=policy,
        sources=sources,
    )
    if batch.errors:
        return HandoffWizardPhaseResult(
            phase="backup",
            status="failed",
            summary=f"Backup finished with {len(batch.errors)} error(s).",
            detail="; ".join(batch.errors[:3]),
        )
    if not batch.executed_count:
        return HandoffWizardPhaseResult(
            phase="backup",
            status="failed",
            summary="No source backups executed.",
            detail=", ".join(sources),
        )
    verification = verify_written_backup_batch(batch)
    if verification.failed:
        return HandoffWizardPhaseResult(
            phase="backup",
            status="failed",
            summary=(
                f"Wrote {batch.executed_count} backup(s) but verification failed for "
                f"{verification.failed} newly written ID(s)."
            ),
            detail="; ".join(verification.issues[:3]) or ", ".join(sources),
        )
    return HandoffWizardPhaseResult(
        phase="backup",
        status="ok",
        summary=(
            f"Executed and verified {verification.verified} full backup(s) "
            f"for {len(sources)} source(s)."
        ),
        detail=", ".join(sources),
    )


def run_handoff_verify_phase(*, execute: bool = True) -> HandoffWizardPhaseResult:
    if not execute:
        return HandoffWizardPhaseResult(
            phase="verify",
            status="skipped",
            summary="Would verify all backup sources and update manifests.",
        )
    blocked = _maintenance_block_phase("verify")
    if blocked is not None:
        return blocked
    summary = run_verify_all_for_menu(update_manifest=True)
    if summary.failed or summary.missing:
        return HandoffWizardPhaseResult(
            phase="verify",
            status="failed",
            summary=(
                f"Verification incomplete — {summary.verified} verified, "
                f"{summary.missing} missing, {summary.failed} failed."
            ),
        )
    return HandoffWizardPhaseResult(
        phase="verify",
        status="ok",
        summary=f"All {summary.verified} source backup(s) verified on operator storage.",
    )


def run_handoff_repo_bundle_phase(*, execute: bool = True) -> HandoffWizardPhaseResult:
    if not execute:
        return HandoffWizardPhaseResult(
            phase="repo_bundle",
            status="skipped",
            summary="Would write repository bundles to operator storage.",
        )
    blocked = _maintenance_block_phase("repo_bundle")
    if blocked is not None:
        return blocked
    from mercury.repo.config import load_repo_bundle_settings, load_repo_definitions
    from mercury.repo.status import inspect_repositories

    plan = build_repo_bundle_plan(
        inspect_repositories(load_repo_definitions()),
        load_repo_bundle_settings(),
    )
    if not plan.entries:
        return HandoffWizardPhaseResult(
            phase="repo_bundle",
            status="skipped",
            summary="No repository entries configured for bundling.",
        )
    if not execute:
        return HandoffWizardPhaseResult(
            phase="repo_bundle",
            status="skipped",
            summary=f"Would write {len(plan.entries)} repository bundle(s) to operator storage.",
        )
    try:
        execute_repo_bundle_plan(plan)
    except ValueError as exc:
        return HandoffWizardPhaseResult(
            phase="repo_bundle",
            status="failed",
            summary="Repository bundle write failed.",
            detail=str(exc),
        )
    errors = [entry.error for entry in plan.entries if entry.error]
    if errors:
        return HandoffWizardPhaseResult(
            phase="repo_bundle",
            status="failed",
            summary=f"Repository bundle finished with {len(errors)} error(s).",
            detail="; ".join(errors[:3]),
        )
    dirty = sum(1 for entry in plan.entries if entry.dirty)
    summary = f"Wrote {len(plan.entries)} repository bundle(s) to operator storage."
    if dirty:
        summary += f" {dirty} repo(s) were dirty at bundle time."
    return HandoffWizardPhaseResult(phase="repo_bundle", status="ok", summary=summary)


def run_handoff_db_bundle_phase(
    *,
    live: bool | None = None,
    execute: bool = True,
    force: bool = False,
    confirm=None,
) -> HandoffWizardPhaseResult:
    use_live = should_probe_database_status() if live is None else live
    plan = build_database_bundle_plan(live=use_live)
    package_status = bundle_package_status(plan)
    if not execute:
        return HandoffWizardPhaseResult(
            phase="db_bundle",
            status="skipped",
            summary=f"Would write database bundle index (package: {package_status}).",
        )
    blocked = _maintenance_block_phase("db_bundle")
    if blocked is not None:
        return blocked
    if handoff_write_requires_force(package_status) and not force:
        prompt = handoff_write_ack_prompt(package_status)
        if confirm is not None and confirm(prompt, default=False) is not True:
            return HandoffWizardPhaseResult(
                phase="db_bundle",
                status="cancelled",
                summary="Database bundle write cancelled.",
            )
        force = True
    try:
        write_database_bundle_plan(plan)
    except ValueError as exc:
        return HandoffWizardPhaseResult(
            phase="db_bundle",
            status="failed",
            summary="Database bundle write failed.",
            detail=str(exc),
        )
    return HandoffWizardPhaseResult(
        phase="db_bundle",
        status="ok",
        summary=f"Database bundle index written (package: {package_status}).",
    )


def run_handoff_transfer_phase(
    *,
    live: bool | None = None,
    execute: bool = True,
    force: bool = False,
    confirm=None,
) -> HandoffWizardPhaseResult:
    use_live = resolve_transfer_live(
        live=should_probe_database_status() if live is None else bool(live),
        seed=False,
    )
    bundle = build_transfer_bundle(live=use_live)
    handoff_status = handoff_status_for_bundle(bundle)
    if not execute:
        return HandoffWizardPhaseResult(
            phase="transfer",
            status="skipped",
            summary=f"Would write combined transfer package (handoff: {handoff_status}).",
        )
    blocked = _maintenance_block_phase("transfer")
    if blocked is not None:
        return blocked
    if handoff_write_requires_force(handoff_status) and not force:
        prompt = handoff_write_ack_prompt(handoff_status)
        if confirm is not None and confirm(prompt, default=False) is not True:
            return HandoffWizardPhaseResult(
                phase="transfer",
                status="cancelled",
                summary="Transfer write cancelled.",
            )
        force = True
    try:
        write_transfer_bundle(bundle)
    except ValueError as exc:
        return HandoffWizardPhaseResult(
            phase="transfer",
            status="failed",
            summary="Transfer write failed.",
            detail=str(exc),
        )
    return HandoffWizardPhaseResult(
        phase="transfer",
        status="ok",
        summary=f"Combined transfer package written (handoff: {handoff_status}).",
    )


def run_guided_handoff_wizard(
    *,
    live: bool | None = None,
    execute: bool = True,
    force: bool = False,
    confirm=None,
    stop_on_failure: bool = True,
    start_phase: str | None = None,
    end_phase: str | None = None,
) -> HandoffWizardResult:
    """Run the full handoff pipeline in order."""
    result = HandoffWizardResult()
    phase_runners = {
        "backup": lambda: run_handoff_backup_phase(live=live, execute=execute),
        "verify": lambda: run_handoff_verify_phase(execute=execute),
        "repo_bundle": lambda: run_handoff_repo_bundle_phase(execute=execute),
        "db_bundle": lambda: run_handoff_db_bundle_phase(
            live=live, execute=execute, force=force, confirm=confirm
        ),
        "transfer": lambda: run_handoff_transfer_phase(
            live=live, execute=execute, force=force, confirm=confirm
        ),
    }
    selected_phases = resolve_wizard_phase_range(
        start_phase=start_phase,
        end_phase=end_phase,
    )
    for phase_key in selected_phases:
        phase_result = phase_runners[phase_key]()
        result.phases.append(phase_result)
        if phase_result.status in {"failed", "cancelled"}:
            result.cancelled = phase_result.status == "cancelled"
            if stop_on_failure:
                break
    checklist = build_handoff_checklist(live=live)
    result.final_handoff_status = checklist.handoff_status
    if execute:
        from mercury.handoff.snapshot import clear_handoff_snapshot
        from mercury.state.ledger import record_handoff_wizard_run

        record_handoff_wizard_run(result)
        clear_handoff_snapshot()
    return result
