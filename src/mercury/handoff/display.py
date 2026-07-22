"""Handoff terminal formatting helpers."""

from __future__ import annotations

from collections import Counter

from mercury.handoff.checklist import HandoffChecklist, HandoffStep
from mercury.terminal.screen import StatusKind


HANDOFF_SCREEN_TITLE = "Workstation Handoff"
HISTORY_SCREEN_TITLE = "Handoff History"
RECEIVER_SCREEN_TITLE = "Receiving Workstation Guide"

_PIPELINE_PHASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("backup", "Backup", ("usb_root", "backups_verified", "backup_freshness")),
    ("repo", "Repos", ("repo_bundles",)),
    ("bundle", "DB bundle", ("db_bundle_index", "manifest_freshness")),
    ("transfer", "Transfer", ("transfer_package",)),
)

_WIZARD_PHASE_KEYS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("backup", "Backup", ("usb_root", "backups_verified", "backup_freshness")),
    ("verify", "Verify", ("backups_verified",)),
    ("repo_bundle", "Repo", ("repo_bundles",)),
    ("db_bundle", "DB bundle", ("db_bundle_index", "manifest_freshness")),
    ("transfer", "Transfer", ("transfer_package",)),
)

_WIZARD_RESULT_SYMBOLS: dict[str, str] = {
    "ok": "[ok]",
    "skipped": "[--]",
    "failed": "[!!]",
    "cancelled": "[!!]",
}


def short_handoff_action(action: str | None) -> str:
    if not action:
        return "—"
    if action.startswith("Handoff ["):
        return action.removeprefix("Handoff ")
    if action.startswith("Handoff menu ["):
        return action.removeprefix("Handoff menu ")
    if action.startswith("./run.sh "):
        return action.removeprefix("./run.sh ")
    return action


def _phase_status_symbol(statuses: list[str]) -> str:
    if not statuses:
        return "[  ]"
    if any(status == "fail" for status in statuses):
        return "[!!]"
    if any(status == "warn" for status in statuses):
        return "[--]"
    if all(status == "ok" for status in statuses):
        return "[ok]"
    return "[  ]"


def handoff_pipeline_line(checklist: HandoffChecklist) -> str:
    """Compact source-workstation pipeline status for menus and dashboards."""
    steps_by_key = {step.step_key: step for step in checklist.steps if step.step_key}
    parts: list[str] = []
    for _phase_key, label, step_keys in _PIPELINE_PHASES:
        statuses = [steps_by_key[key].status for key in step_keys if key in steps_by_key]
        parts.append(f"{_phase_status_symbol(statuses)} {label}")
    return " · ".join(parts)


def handoff_wizard_plan_line(
    checklist: HandoffChecklist,
    *,
    start_phase: str | None = None,
    end_phase: str | None = None,
) -> str:
    """Wizard phase rail from checklist readiness (before or between runs)."""
    from mercury.handoff.wizard import resolve_wizard_phase_range

    steps_by_key = {step.step_key: step for step in checklist.steps if step.step_key}
    selected = set(resolve_wizard_phase_range(start_phase=start_phase, end_phase=end_phase))
    parts: list[str] = []
    for phase_key, label, step_keys in _WIZARD_PHASE_KEYS:
        if phase_key not in selected:
            continue
        statuses = [steps_by_key[key].status for key in step_keys if key in steps_by_key]
        parts.append(f"{_phase_status_symbol(statuses)} {label}")
    return " → ".join(parts) if parts else "No wizard phases selected."


def wizard_result_progress_line(result) -> str:
    """Wizard phase rail after a guided run (includes skipped/failed markers)."""
    from mercury.handoff.wizard import wizard_phase_choices

    completed = {phase.phase: phase.status for phase in result.phases}
    parts: list[str] = []
    for phase_key in wizard_phase_choices():
        label = next((name for key, name, _keys in _WIZARD_PHASE_KEYS if key == phase_key), phase_key)
        status = completed.get(phase_key)
        if status is None:
            parts.append(f"[  ] {label}")
        else:
            parts.append(f"{_WIZARD_RESULT_SYMBOLS.get(status, '[  ]')} {label}")
    return " → ".join(parts)


def wizard_progress_summary(result) -> str:
    """Compact counts for wizard phase results."""
    if not result.phases:
        return "No wizard phases run."
    ok_count = sum(1 for phase in result.phases if phase.status == "ok")
    skip_count = sum(1 for phase in result.phases if phase.status == "skipped")
    fail_count = sum(
        1 for phase in result.phases if phase.status in {"failed", "cancelled"}
    )
    parts: list[str] = []
    if ok_count:
        parts.append(f"{ok_count} OK")
    if skip_count:
        parts.append(f"{skip_count} skip")
    if fail_count:
        parts.append(f"{fail_count} fail")
    parts.append(f"{len(result.phases)} phase(s) run")
    return " · ".join(parts)


def suggested_menu_choice(checklist: HandoffChecklist) -> str | None:
    """Best handoff submenu key for the current checklist state."""
    primary = primary_handoff_action(checklist)
    if not primary:
        return None
    if checklist.handoff_status == "complete":
        return "11"
    import re

    match = re.search(r"\[(\d+)\]", primary)
    if match:
        return match.group(1)
    if "guided wizard" in primary.lower():
        return "2"
    return None


def handoff_dashboard_line(
    *,
    verified_count: int,
    source_count: int,
    stale_count: int = 0,
    missing_count: int = 0,
    failed_count: int = 0,
    unknown_count: int = 0,
    absent_count: int = 0,
    latest_handoff_status: str | None = None,
    latest_transfer_at: str | None = None,
) -> str:
    """One-line handoff status for the main menu dashboard."""
    effective_sources = max(0, source_count - max(0, absent_count))
    if missing_count or failed_count:
        line = "[!!] partial — [10] checklist · [2] wizard"
    elif stale_count:
        line = "[--] stale backups — [4] refresh backup lane"
    elif unknown_count:
        line = "[--] unknown freshness — [2] guided wizard"
    elif absent_count and effective_sources and verified_count >= effective_sources:
        line = "[--] ready with absent sources — [10] checklist"
    elif effective_sources and verified_count == effective_sources:
        line = "[ok] ready — [10] handoff · [2] wizard"
    else:
        line = "[--] incomplete — [10] checklist"
    if latest_transfer_at:
        line += f" · last transfer {latest_transfer_at}"
    if latest_handoff_status and latest_handoff_status != "complete":
        line += f" ({latest_handoff_status})"
    return line


def receiver_handoff_steps(*, checklist: HandoffChecklist | None = None) -> list[tuple[str, str, str]]:
    """Receiver checklist rows: phase, status hint, action."""
    manifest = checklist.latest_transfer_manifest if checklist else None
    transfer_age = checklist.latest_transfer_age if checklist else None
    db_bundle_age = checklist.latest_database_bundle_age if checklist else None
    handoff_status = checklist.handoff_status if checklist else "unknown"
    manifest_detail = manifest or "mount operator storage and locate mercury_manifests/"
    return [
        (
            "Mount operator storage media",
            "ok" if manifest else "warn",
            "Confirm mercury_backups, mercury_manifests, and mercury_runbooks are present.",
        ),
        (
            "Install Mercury on receiver",
            "ok",
            "Clone or copy Mercury, then run ./run.sh config init.",
        ),
        (
            "Validate receiver environment",
            "ok",
            "Run ./run.sh doctor and ./run.sh db ping before any import.",
        ),
        (
            "Review transfer package",
            "ok" if handoff_status == "complete" else "warn",
            (
                f"Latest transfer package: {transfer_age or 'not found'}"
                f"{f' ({manifest_detail})' if manifest else ''}"
            ),
        ),
        (
            "Import database backups",
            "ok" if handoff_status == "complete" else "warn",
            "Run ./run.sh deploy system to import verified operator-storage database backups.",
        ),
        (
            "Restore repository bundles",
            "ok",
            "Run ./run.sh deploy repos --from-usb for operator-storage Git bundles.",
        ),
        (
            "Open database runbooks",
            "ok" if db_bundle_age else "warn",
            f"Latest DB bundle index: {db_bundle_age or 'not found on operator storage'}.",
        ),
        (
            "Run restore-check drills",
            "ok",
            "Use restore-check against verified backups before relying on production paths.",
        ),
    ]


def handoff_status_kind(status: str) -> StatusKind:
    normalized = status.strip().lower()
    if normalized == "complete":
        return "ok"
    if "warning" in normalized:
        return "warn"
    if normalized.startswith("blocked") or normalized in {"partial", "empty"}:
        return "fail"
    return "info"


def receiver_quick_start_lines() -> list[str]:
    return [
        "Mount this media on the receiving workstation.",
        "Run ./run.sh config init and ./run.sh doctor on the receiver.",
        "Run ./run.sh deploy system to import verified database backups.",
        "Run ./run.sh deploy repos --from-usb for operator-storage repository bundles.",
        "Start with the latest transfer runbook on operator storage before any live restore.",
    ]


def step_status_kind(status: str) -> StatusKind:
    mapping: dict[str, StatusKind] = {
        "ok": "ok",
        "warn": "warn",
        "fail": "fail",
        "skip": "info",
        "skipped": "info",
        "cancelled": "warn",
    }
    return mapping.get(status, "info")


def step_status_label(status: str) -> str:
    mapping = {
        "ok": "OK",
        "warn": "Warn",
        "fail": "Fail",
        "skip": "Skip",
        "skipped": "Skip",
        "cancelled": "Stop",
    }
    return mapping.get(status, status.title())


def step_progress_summary(steps: list[HandoffStep]) -> str:
    counts = Counter(step.status for step in steps)
    parts: list[str] = []
    if counts["ok"]:
        parts.append(f"{counts['ok']} OK")
    if counts["warn"]:
        parts.append(f"{counts['warn']} Warn")
    if counts["fail"]:
        parts.append(f"{counts['fail']} Fail")
    if not parts:
        return "No checklist steps available."
    return " · ".join(parts)


def primary_handoff_action(checklist: HandoffChecklist) -> str | None:
    if checklist.handoff_status == "complete":
        return "Operator storage is ready — move to the receiving workstation and open the latest transfer runbook."
    for step in checklist.steps:
        if step.status == "fail" and step.action:
            return step.action
    for step in checklist.steps:
        if step.status == "warn" and step.action:
            return step.action
    actions = checklist.recommended_actions()
    return actions[0] if actions else None
