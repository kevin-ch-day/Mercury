"""Terminal rendering for Mercury transfer summaries."""

from __future__ import annotations

from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.transfer.bundle import TransferBundle
from mercury.state.summary import build_state_summary


def print_transfer_bundle(bundle: TransferBundle, *, executed: bool = False) -> None:
    state = build_state_summary()
    verified_sources = sum(1 for entry in bundle.database_entries if entry.verified)
    dirty_repos = sum(1 for entry in bundle.repo_entries if entry.dirty and not entry.error)
    repo_errors = sum(1 for entry in bundle.repo_entries if entry.error)
    repo_bundles_verified = all(
        entry.bundle_verified for entry in bundle.repo_entries if not entry.error and entry.bundle_path
    )
    repo_bundles_present = all(
        entry.bundle_path and entry.repo_manifest_path and entry.repo_runbook_path
        for entry in bundle.repo_entries
        if not entry.error
    ) if bundle.repo_entries else False
    database_package = "complete" if bundle.database_entries and verified_sources == len(bundle.database_entries) else "partial"
    repository_package = "complete"
    if repo_errors or not repo_bundles_present or not repo_bundles_verified:
        repository_package = "partial"
    elif dirty_repos:
        repository_package = "complete with warnings"
    sync_status = "ready" if bundle.ready_sync_pairs and bundle.blocked_sync_pairs == 0 else "blocked"
    transfer_complete = bool(bundle.latest_transfer_manifest_path and bundle.latest_transfer_runbook_path)
    display_screen.write_fields(
        {
            "Mode": bundle.mode.upper(),
            "Database sources": len(bundle.database_entries),
            "Verified sources": verified_sources,
            "Configured repos": len(bundle.repo_entries),
            "Dirty repos": dirty_repos,
            "Repo errors": repo_errors,
            "Sync ready": bundle.ready_sync_pairs,
            "Sync blocked": bundle.blocked_sync_pairs,
            "Database package": database_package,
            "Repository package": repository_package,
            "Sync readiness": sync_status,
            "Actual sync": "deferred",
            "Transfer package": "complete" if transfer_complete else "partial",
            "State root": str(state.state_root),
            "State ops": state.operations,
        }
    )

    if bundle.database_entries:
        display_screen.write_blank()
        display_screen.write_structured_table(
            Table.from_headers(
                ["DATABASE", "ROLE", "VERIFIED", "BACKUP ID"],
                [
                    [
                        entry.database,
                        entry.source_role,
                        "yes" if entry.verified else "no",
                        entry.backup_id or "missing",
                    ]
                    for entry in bundle.database_entries
                ],
                style=TableStyle(indent=0),
                min_col_widths=[28, 18, 8, 18],
                max_col_widths=[36, 20, 8, 40],
            )
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
        display_screen.write_summary("Transfer package written to USB.")
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
