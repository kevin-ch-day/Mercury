"""Terminal rendering for Mercury transfer summaries."""

from __future__ import annotations

from mercury.terminal import screen as display_screen
from mercury.transfer.bundle import TransferBundle


def print_transfer_bundle(bundle: TransferBundle) -> None:
    verified_sources = sum(1 for entry in bundle.database_entries if entry.verified)
    dirty_repos = sum(1 for entry in bundle.repo_entries if entry.dirty and not entry.error)
    repo_errors = sum(1 for entry in bundle.repo_entries if entry.error)
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
        }
    )

    if bundle.database_entries:
        display_screen.write_blank()
        display_screen.write_compact_table(
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
            min_col_widths=[28, 18, 8, 18],
            max_col_widths=[36, 20, 8, 40],
        )

    if bundle.repo_entries:
        display_screen.write_blank()
        display_screen.write_compact_table(
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
            min_col_widths=[18, 12, 12, 8],
        )

    display_screen.write_blank()
    display_screen.write_summary(f"Transfer manifest: {bundle.transfer_manifest_path}")
    display_screen.write_summary(f"Transfer runbook: {bundle.transfer_runbook_path}")
