"""Plain-text backup plan output including future layout hints."""

from datetime import datetime, timezone
from pathlib import Path

from mercury import output
from mercury.backup.layout import build_backup_layout, list_standard_filenames
from mercury.backup.live_inventory import (
    fetch_live_server_database_names,
    live_source_missing_reason,
)
from mercury.backup.manifest import BACKUP_KIND_LABELS
from mercury.core.execution_policy import backup_mode_label, load_execution_policy
from mercury.database.backup_planning import BackupPlanDryRun
from mercury.database.core.classifier import DatabaseRole, classify_database


def _display_backup_path(backup_root: Path, relative_path: str) -> str:
    relative = Path(relative_path)
    try:
        relative = relative.relative_to("backups")
    except ValueError:
        pass
    return str((backup_root / relative).resolve())


def _group_backup_sources(plan: BackupPlanDryRun) -> tuple[list[str], list[str]]:
    production: list[str] = []
    shared_authority: list[str] = []
    for name in plan.backup_sources:
        role = classify_database(name).role
        if role == DatabaseRole.SHARED_AUTHORITY:
            shared_authority.append(name)
        else:
            production.append(name)
    return production, shared_authority


def _group_excluded(plan: BackupPlanDryRun) -> tuple[list[object], list[object], list[object]]:
    dev_targets: list[object] = []
    out_of_scope: list[object] = []
    other: list[object] = []
    for item in plan.excluded:
        if "Out of active Mercury scope" in item.reason:
            out_of_scope.append(item)
        elif item.role == DatabaseRole.DEVELOPMENT.value:
            dev_targets.append(item)
        else:
            other.append(item)
    return dev_targets, out_of_scope, other


def _print_backup_source(
    name: str,
    *,
    backup_root: Path,
    plan_date: str,
    plan_timestamp: str,
    show_layout_hint: bool,
    live: bool,
    server_names: set[str] | None,
    shared_authority: bool = False,
) -> None:
    missing_reason = live_source_missing_reason(name, live=live, server_names=server_names)
    output.item(name)
    if shared_authority:
        output.item("backup-only; sync not applicable by design", indent=2)
    if missing_reason:
        output.item("status: missing on server; backup refused", indent=2)
        output.item(missing_reason, indent=4)
        return
    if show_layout_hint:
        layout = build_backup_layout(name, date=plan_date, timestamp=plan_timestamp)
        output.item(
            f"future: {_display_backup_path(backup_root, layout.future_full_hint())}",
            indent=2,
        )


def print_backup_plan(
    plan: BackupPlanDryRun,
    *,
    show_layout_hint: bool = True,
    live: bool = False,
) -> None:
    policy = load_execution_policy()
    server_names = fetch_live_server_database_names() if live else None
    instant = datetime.now(timezone.utc)
    plan_date = instant.strftime("%Y-%m-%d")
    plan_timestamp = instant.strftime("%Y%m%d_%H%M%S") + f"_{instant.microsecond // 1000:03d}"
    output.heading("Backup plan (preview)")
    output.field("backup root", str(policy.backup_root.resolve()))
    output.field("backup root state", policy.backup_root_state())
    output.field("backup mode", backup_mode_label(policy))
    if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
        from mercury.core.usb_mount import resolve_operator_mount

        mount = resolve_operator_mount()
        output.field(
            "warning",
            f"Repo-local fallback only; configure backup_root under {mount} before live backups "
            "(configured active writer). See ./run.sh storage status.",
        )

    production_sources, shared_authority_sources = _group_backup_sources(plan)
    excluded_dev, _out_of_scope, other_excluded = _group_excluded(plan)

    output.heading("Production sources")
    if production_sources:
        for name in production_sources:
            _print_backup_source(
                name,
                backup_root=policy.backup_root,
                plan_date=plan_date,
                plan_timestamp=plan_timestamp,
                show_layout_hint=show_layout_hint,
                live=live,
                server_names=server_names,
            )
    else:
        output.item("(none)")

    output.heading("Shared authority sources")
    if shared_authority_sources:
        for name in shared_authority_sources:
            _print_backup_source(
                name,
                backup_root=policy.backup_root,
                plan_date=plan_date,
                plan_timestamp=plan_timestamp,
                show_layout_hint=show_layout_hint,
                live=live,
                server_names=server_names,
                shared_authority=True,
            )
    else:
        output.item("(none)")

    output.heading("Excluded development targets")
    if excluded_dev:
        for item in excluded_dev:
            output.item(f"{item.name} [{item.role}]")
            output.item(item.reason, indent=4)
    else:
        output.item("(none)")

    if other_excluded:
        output.heading("Other excluded databases")
        for item in other_excluded:
            output.item(f"{item.name} [{item.role}]")
            output.item(item.reason, indent=4)

    if show_layout_hint and plan.backup_sources:
        example = plan.backup_sources[0]
        layout = build_backup_layout(example, date=plan_date, timestamp=plan_timestamp)
        output.heading("Future backup layout (preview only)")
        output.item(_display_backup_path(policy.backup_root, layout.directory))
        for fname in list_standard_filenames(example, layout.timestamp):
            output.item(fname, indent=2)
        output.heading("Backup kinds")
        for kind, label in BACKUP_KIND_LABELS.items():
            output.item(f"{kind}: {label}")

    output.heading("Safety notes")
    for note in plan.safety_notes:
        output.bullet(note)
