"""Terminal output for repository deployment batch results."""

from __future__ import annotations

from mercury.deploy.repos.models import RepoDeployBatchResult
from mercury.terminal import screen as display_screen


def print_repo_deploy_summary(batch: RepoDeployBatchResult) -> None:
    display_screen.write_section("REPOSITORY DEPLOYMENT SUMMARY")
    display_screen.write_fields(
        {
            "Mode": batch.mode,
            "Host": batch.hostname,
            "Source mode": batch.source_mode,
            "Deployed": str(batch.deployed_count),
            "Skipped": str(batch.skipped_count),
            "Failed": str(batch.failed_count),
        }
    )
    for result in batch.results:
        if result.executed:
            tag = "ok"
        elif result.skipped:
            tag = "warn"
        elif result.dry_run:
            tag = "info"
        else:
            tag = "fail"
        line = f"{result.display_name}: {result.message}"
        display_screen.write_status(tag, line)
    if batch.report_path:
        display_screen.write_summary(f"Report: {batch.report_path}")
