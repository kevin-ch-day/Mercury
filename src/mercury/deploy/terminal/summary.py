"""Terminal output for deployment batch results."""

from __future__ import annotations

from mercury.deploy.models import DeploymentBatchResult
from mercury.terminal import screen as display_screen


def print_deployment_summary(batch: DeploymentBatchResult) -> None:
    display_screen.write_section("DEPLOYMENT SUMMARY")
    display_screen.write_fields(
        {
            "Mode": batch.mode,
            "Host": batch.hostname,
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
        display_screen.write_status(
            tag,
            f"{result.target_database}: {result.message}",
        )
        if result.verification is not None:
            display_screen.write_summary(f"  verification: {result.verification.detail}")
    if batch.report_path:
        display_screen.write_summary(f"Report: {batch.report_path}")
    display_screen.write_blank()
    display_screen.write_summary("Next:")
    display_screen.write_summary("  ./run.sh db inventory")
    display_screen.write_summary("  ./run.sh doctor")
    display_screen.write_summary("  ./run.sh menu")
