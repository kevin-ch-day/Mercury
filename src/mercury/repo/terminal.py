"""Terminal rendering for repository status and bundle planning."""

from __future__ import annotations

from mercury.terminal import screen as display_screen
from mercury.repo.bundle import RepoBundlePlan
from mercury.repo.status import RepoStatus, summarize_repo_statuses
from mercury.state.summary import build_state_summary


def print_repo_statuses(statuses: list[RepoStatus], *, verbose: bool = False) -> None:
    summary = summarize_repo_statuses(statuses)
    display_screen.write_fields(
        {
            "Configured repos": summary.configured,
            "Clean": summary.clean,
            "Dirty": summary.dirty,
            "Errors": summary.errors,
        }
    )
    if not statuses:
        display_screen.write_status("warn", "No repositories configured. Run: mercury repo init-config")
        return

    rows: list[list[str]] = []
    for status in statuses:
        rows.append(
            [
                status.display_name,
                status.branch,
                status.commit[:12] if status.commit != "unknown" else status.commit,
                status.state_label,
                "included" if status.migration_scope else "excluded",
                str(status.untracked_count),
                status.upstream_label,
            ]
        )
    display_screen.write_blank()
    display_screen.write_compact_table(
        ["REPOSITORY", "BRANCH", "COMMIT", "STATE", "MIGRATION", "UNTRACKED", "UPSTREAM"],
        rows,
        min_col_widths=[18, 12, 12, 8, 10, 9, 10],
    )
    if verbose:
        for status in statuses:
            display_screen.write_blank()
            display_screen.write_fields(
                {
                    "Repository": status.display_name,
                    "Path": status.path,
                    "Remote": status.remote_url,
                    "Migration scope": "included" if status.migration_scope else "excluded",
                }
            )
            if status.error:
                display_screen.write_status("fail", status.error)
    if summary.dirty or summary.with_untracked:
        display_screen.write_blank()
        display_screen.write_summary(
            "Dirty repos and untracked files are reported here, but Git bundles include committed history only."
        )


def print_repo_bundle_plan(plan: RepoBundlePlan, *, executed: bool = False) -> None:
    state = build_state_summary()
    dirty = sum(1 for entry in plan.entries if entry.dirty and not entry.error)
    errors = sum(1 for entry in plan.entries if entry.error)
    display_screen.write_fields(
        {
            "Repo bundle root": plan.repo_backup_root,
            "Manifest dir": plan.manifest_dir,
            "Runbook dir": plan.runbook_dir,
            "Repositories": len(plan.entries),
            "Dirty repos": dirty,
            "Errors": errors,
            "State root": str(state.state_root),
            "State ops": state.operations,
        }
    )
    display_screen.write_blank()
    rows: list[list[str]] = []
    for entry in plan.entries:
        status = "written" if executed and entry.executed else "planned"
        if entry.error:
            status = "error"
        rows.append(
            [
                entry.display_name,
                entry.commit[:12] if entry.commit != "unknown" else entry.commit,
                status,
                "dirty" if entry.dirty else "clean",
            ]
        )
    display_screen.write_compact_table(
        ["REPOSITORY", "COMMIT", "STATUS", "WORKTREE"],
        rows,
        min_col_widths=[18, 12, 8, 8],
    )
    display_screen.write_blank()
    display_screen.write_summary(
        f"Transfer index manifest: {plan.planned_index_manifest_path}"
    )
    display_screen.write_summary(
        f"Transfer index runbook: {plan.planned_index_runbook_path}"
    )
    if dirty:
        display_screen.write_summary(
            "Dirty or untracked repo content is not included in Git bundle contents."
        )
    if executed:
        display_screen.write_blank()
        display_screen.write_summary(
            "Bundles, per-repo manifests, transfer index manifest, and restore notes were written to the USB paths above."
        )
        display_screen.write_summary(
            "Repo retention keeps one current verified bundle set per repo; older repo artifacts are pruned after successful replacement."
        )
