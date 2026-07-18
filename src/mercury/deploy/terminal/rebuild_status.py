"""Terminal output for post-rebuild status."""

from __future__ import annotations

from mercury.deploy.rebuild_status import RebuildStatusReport
from mercury.terminal import screen as display_screen


def print_rebuild_status(report: RebuildStatusReport) -> None:
    display_screen.write_section("REBUILD STATUS")
    display_screen.write_fields(
        {
            "Host": report.hostname,
            "Databases": f"{report.databases_deployed} of {report.databases_total} on server",
            "Repositories": f"{report.repositories_deployed} of {report.repositories_total} deployed",
            "Storage": "healthy" if report.usb_healthy else "needs repair",
            "MariaDB": "connected" if report.mariadb_connected else "not connected",
            "Backup history": report.verified_backups,
            "Deploy needed": "yes" if report.deployment_needed else "no",
            "Deploy status": report.deploy_status,
            "Sync pairs": f"{report.sync_ready} ready, {report.sync_blocked} need dev targets",
            "Sync blocker": report.sync_blocker,
        }
    )
    if report.repositories_missing:
        display_screen.write_status(
            "warn",
            "Missing repositories: " + ", ".join(report.repositories_missing),
        )
    if report.cleanup_suggestions:
        display_screen.write_blank()
        display_screen.write_summary("Cleanup suggestions (not run automatically):")
        for command in report.cleanup_suggestions:
            display_screen.write_summary(f"  {command}")
    display_screen.write_blank()
    display_screen.write_summary(f"Recommended next: {report.recommended_next}")
