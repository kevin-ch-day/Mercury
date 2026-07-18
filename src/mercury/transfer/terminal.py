"""Terminal rendering for Mercury transfer summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mercury.backup.freshness import display_freshness_label, handoff_freshness_warning
from mercury.terminal import screen as display_screen
from mercury.terminal.format import format_human_datetime
from mercury.terminal.table import Table, TableStyle
from mercury.transfer.bundle import (
    TransferBundle,
    database_package_status_for_bundle,
    handoff_status_for_bundle,
    repository_package_status_for_bundle,
)
from mercury.state.summary import build_state_summary


def _usb_artifact_summary(path_str: str | None) -> str:
    if not path_str:
        return "none on operator storage"
    path = Path(path_str)
    if not path.is_file():
        return "listed path missing on operator storage"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = format_human_datetime(mtime.isoformat())
    return f"{age} ({path.name})"


def _sync_readiness_label(bundle: TransferBundle) -> str:
    pair_count = bundle.ready_sync_pairs + bundle.blocked_sync_pairs
    if pair_count == 0:
        return "n/a"
    if bundle.ready_sync_pairs and bundle.blocked_sync_pairs == 0:
        return "ready"
    return "blocked"


def print_transfer_bundle(bundle: TransferBundle, *, executed: bool = False) -> None:
    from mercury.handoff.checklist import build_handoff_checklist_from_bundle
    from mercury.handoff.display import handoff_pipeline_line, handoff_status_kind

    display_screen.open_screen("Transfer Package")
    state = build_state_summary()
    verified_sources = bundle.verified_source_count
    source_count = len(bundle.database_entries)
    database_package = database_package_status_for_bundle(bundle)
    dirty_repos = sum(1 for entry in bundle.repo_entries if entry.dirty and not entry.error)
    repository_package = repository_package_status_for_bundle(bundle)
    handoff_status = handoff_status_for_bundle(bundle)
    sync_status = _sync_readiness_label(bundle)
    checklist = build_handoff_checklist_from_bundle(bundle, state_bundle_rows=state.database_bundle_rows)
    display_screen.write_status(
        handoff_status_kind(handoff_status),
        f"Handoff readiness: {handoff_status}",
    )
    display_screen.write_blank()
    display_screen.write_fields(
        {
            "Pipeline": handoff_pipeline_line(checklist),
            "Mode": bundle.mode.upper(),
            "Database sources": source_count,
            "Verified sources": verified_sources,
            "Freshness": (
                f"{bundle.stale_source_count} stale · {bundle.unknown_freshness_source_count} unknown"
                if source_count
                else "—"
            ),
            "Configured repos": len(bundle.repo_entries),
            "Dirty repos": dirty_repos,
            "Repo errors": sum(1 for entry in bundle.repo_entries if entry.error),
            "Sync ready": bundle.ready_sync_pairs,
            "Sync blocked": bundle.blocked_sync_pairs,
            "Database package": database_package,
            "Repository package": repository_package,
            "Handoff readiness": handoff_status,
            "Sync readiness": sync_status,
            "Actual sync": "deferred",
            "Latest on operator storage": _usb_artifact_summary(bundle.latest_transfer_manifest_path),
            "State root": str(state.state_root),
            "State ops": state.operations,
            "State bundles": state.database_bundle_rows,
        }
    )

    if bundle.database_entries:
        display_screen.write_blank()
        display_screen.write_structured_table(
            Table.from_headers(
                ["DATABASE", "ROLE", "VERIFIED", "FRESH", "BACKUP ID"],
                [
                    [
                        entry.database,
                        entry.source_role,
                        "yes" if entry.verified else "no",
                        display_freshness_label(entry.freshness),
                        entry.backup_id or "missing",
                    ]
                    for entry in bundle.database_entries
                ],
                style=TableStyle(indent=0),
                min_col_widths=[28, 18, 8, 8, 28],
            )
        )

    freshness_warning = handoff_freshness_warning(
        stale_count=bundle.stale_source_count,
        unknown_count=bundle.unknown_freshness_source_count,
    )
    if freshness_warning:
        display_screen.write_blank()
        display_screen.write_status("warn", freshness_warning)
    if handoff_status != "complete":
        display_screen.write_blank()
        display_screen.write_status(
            "warn",
            f"Current snapshot is {handoff_status} — refresh backups/repo bundles before handoff, "
            "or use --force when writing.",
        )

    if bundle.repo_entries:
        display_screen.write_blank()
        display_screen.write_structured_table(
            Table.from_headers(
                ["REPOSITORY", "BRANCH", "COMMIT", "STATE"],
                [
                    [
                        entry.repo_name,
                        entry.branch,
                        entry.commit[:12] if entry.commit != "unknown" else entry.commit,
                        ("error" if entry.error else "dirty" if entry.dirty else "clean"),
                    ]
                    for entry in bundle.repo_entries
                ],
                style=TableStyle(indent=0),
                min_col_widths=[18, 12, 12, 8],
                max_col_widths=[24, 12, 12, 8],
            )
        )

    display_screen.write_blank()
    if executed:
        display_screen.write_summary(f"Transfer manifest written: {bundle.transfer_manifest_path}")
        display_screen.write_summary(f"Transfer runbook written: {bundle.transfer_runbook_path}")
        display_screen.write_summary("Transfer package written to operator storage.")
    else:
        if bundle.latest_transfer_manifest_path:
            display_screen.write_summary(f"Latest transfer manifest: {bundle.latest_transfer_manifest_path}")
        else:
            display_screen.write_summary(f"Planned transfer manifest: {bundle.transfer_manifest_path}")
        if bundle.latest_transfer_runbook_path:
            display_screen.write_summary(f"Latest transfer runbook: {bundle.latest_transfer_runbook_path}")
        else:
            display_screen.write_summary(f"Planned transfer runbook: {bundle.transfer_runbook_path}")
    if bundle.warnings:
        display_screen.write_blank()
        for warning in bundle.warnings:
            display_screen.write_summary(f"Warning: {warning}")
