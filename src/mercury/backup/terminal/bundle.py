"""Terminal output for database backup transfer bundle planning."""

from __future__ import annotations

from pathlib import Path

from mercury.backup.bundle import DatabaseBundleEntry, DatabaseBundlePlan, bundle_package_status
from mercury.backup.freshness import (
    display_artifact_status_label,
    display_freshness_label,
    handoff_freshness_warning,
)
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.state.summary import build_state_summary


def _rel_artifact(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return path.name


def _databases_with_freshness(entries: list[DatabaseBundleEntry], freshness: str) -> list[str]:
    return [entry.database for entry in entries if entry.freshness == freshness]


def _bundle_table_rows(plan: DatabaseBundlePlan) -> list[list[str]]:
    rows: list[list[str]] = []
    for entry in plan.entries:
        rows.append(
            [
                entry.database,
                entry.role,
                display_artifact_status_label(entry.protection_status),
                display_freshness_label(entry.freshness),
                entry.backup_age or "—",
                entry.backup_id or "—",
            ]
        )
    return rows


def _written_artifact_rows(plan: DatabaseBundlePlan) -> list[list[str]]:
    rows: list[list[str]] = []
    for entry in plan.entries:
        rows.append(
            [
                entry.database,
                _rel_artifact(entry.planned_manifest_path, plan.manifest_dir),
                _rel_artifact(entry.planned_runbook_path, plan.runbook_dir),
            ]
        )
    return rows


def _bundle_next_steps(plan: DatabaseBundlePlan) -> list[str]:
    steps: list[str] = []
    stale_names = _databases_with_freshness(plan.entries, "stale")
    if stale_names:
        steps.append(
            "Run full backup before handoff for stale source(s): "
            + ", ".join(stale_names)
            + "."
        )
    unknown_names = _databases_with_freshness(plan.entries, "unknown")
    if unknown_names:
        steps.append(
            "Freshness is unknown for: "
            + ", ".join(unknown_names)
            + " — run full backup and verify before handoff."
        )
    if plan.missing_count or plan.failed_count:
        steps.append(
            "Missing or failed backups remain in this bundle — run backup and verify before relying on handoff."
        )
    if plan.verified_count and not stale_names and not unknown_names:
        steps.append(
            "On the receiving workstation, open the index runbook and per-database restore notes under the operator runbook dir."
        )
    elif plan.verified_count:
        steps.append(
            "After backups are fresh, use the index runbook and per-database restore notes on the operator runbook dir."
        )
    if not steps:
        steps.append("Bundle is ready for workstation handoff using the index runbook on operator storage.")
    return steps


def print_database_bundle_plan(plan: DatabaseBundlePlan, *, executed: bool = False) -> None:
    title = "Database backup bundle"
    state = build_state_summary()
    package_status = bundle_package_status(plan)
    display_screen.open_screen(title)
    display_screen.write_fields(
        {
            "Backup root": str(plan.backup_root),
            "Manifest dir": str(plan.manifest_dir),
            "Runbook dir": str(plan.runbook_dir),
            "Sources": (
                f"{plan.source_count} total · {plan.verified_count} verified · "
                f"{plan.missing_count} missing · {plan.failed_count} failed"
            ),
            "Freshness": (
                f"{plan.stale_count} stale · {plan.unknown_freshness_count} unknown"
                if plan.source_count
                else "—"
            ),
            "Package": package_status,
            "State root": str(state.state_root),
            "State ops": state.operations,
            "State bundles": state.database_bundle_rows,
        }
    )
    display_screen.write_blank()
    rows = _bundle_table_rows(plan)
    if rows:
        table = Table.from_headers(
            ["DATABASE", "ROLE", "ARTIFACT", "FRESH", "BACKUP AGE", "BACKUP ID"],
            rows,
            style=TableStyle(indent=0),
            min_col_widths=[24, 8, 10, 8, 10, 28],
        )
        display_screen.write_structured_table(table)
    if plan.warnings:
        display_screen.write_blank()
        for warning in plan.warnings:
            display_screen.write_status("warn", warning)
    freshness_warning = handoff_freshness_warning(
        stale_count=plan.stale_count,
        unknown_count=plan.unknown_freshness_count,
    )
    if freshness_warning:
        display_screen.write_blank()
        display_screen.write_status("warn", freshness_warning)
    if plan.missing_count:
        display_screen.write_blank()
        display_screen.write_status(
            "warn",
            f"{plan.missing_count} source(s) missing verified operator-storage backups — bundle index will list gaps.",
        )
    display_screen.write_blank()
    if executed:
        display_screen.write_status("ok", "Bundle written to operator storage.")
        display_screen.write_summary(f"Index manifest: {plan.planned_index_manifest_path}")
        display_screen.write_summary(f"Index runbook: {plan.planned_index_runbook_path}")
        artifact_rows = _written_artifact_rows(plan)
        if artifact_rows:
            display_screen.write_blank()
            display_screen.write_structured_table(
                Table.from_headers(
                    ["DATABASE", "MANIFEST", "RUNBOOK"],
                    artifact_rows,
                    style=TableStyle(indent=0),
                    min_col_widths=[24, 28, 28],
                )
            )
        display_screen.write_blank()
        display_screen.write_summary("Next steps:")
        for step in _bundle_next_steps(plan):
            display_screen.write_summary(f"- {step}")
    else:
        display_screen.write_summary(f"Planned index manifest: {plan.planned_index_manifest_path}")
        display_screen.write_summary(f"Planned index runbook: {plan.planned_index_runbook_path}")
        display_screen.write_summary(
            "Confirm write to operator storage to create per-database manifests and restore runbooks."
        )
