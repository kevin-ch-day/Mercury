"""Display sync readiness report."""

from mercury import output
from mercury.sync.readiness import SyncReadinessReport


def print_sync_readiness_report(report: SyncReadinessReport) -> None:
    output.heading("SYNC READINESS (verified prod backup required)")
    output.field("mode", report.mode)
    output.field("backup_root", report.backup_root)
    output.field("ready", report.ready_count)
    output.field("blocked", report.blocked_count)

    for entry in report.entries:
        output.write()
        project = f" [{entry.project}]" if entry.project else ""
        status = "READY" if entry.ready_for_sync_planning else "BLOCKED"
        output.write(f"- {entry.prod}{project} -> {entry.expected_dev} [{status}]")
        if entry.latest_backup_dir:
            output.write(f"  latest_backup: {entry.latest_backup_dir}")
        if entry.backup_id:
            output.write(f"  backup_id: {entry.backup_id}")
        output.write(f"  backup_verified: {entry.backup_verified}")
        for blocker in entry.blockers:
            output.write(f"  - {blocker}")

    output.write()
    output.write("Sync execution is not implemented yet. This report is planning only.")
