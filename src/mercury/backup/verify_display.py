"""Plain-text display for verification plan, backup list, and report preview."""

from mercury import output
from mercury.backup.list import DemoBackupList
from mercury.reporting.preview import BackupReportPreview, format_report_preview_markdown
from mercury.backup.verification import BackupVerificationResult, VerificationPlan


def print_verification_plan(plan: VerificationPlan) -> None:
    output.write("BACKUP VERIFICATION PLAN")
    output.write("------------------------")
    output.write("Future checks:")
    for index, check in enumerate(plan.future_checks, start=1):
        output.write(f"[{index}] {check}")

    output.write("")
    output.write("Seed status:")
    for status in plan.seed_status:
        output.write(f"- {status}")

    if plan.demo_results:
        output.write("")
        output.write("Demo preview records (not verified):")
        for result in plan.demo_results:
            output.write(
                f"- {result.database} [{result.backup_kind}] "
                f"verified={result.verified} issues={len(result.issues)}"
            )


def print_demo_backup_list(demo_list: DemoBackupList) -> None:
    output.write("BACKUP LIST (demo planned records)")
    output.write("--------------------------------")
    output.write(f"Note: {demo_list.note}")
    output.write("")
    for record in demo_list.records:
        output.write(f"- {record.database} [{record.backup_kind}]")
        output.write(f"  backup_id: {record.backup_id}")
        output.write(f"  directory: {record.planned_directory}")
        if record.planned_dump_file:
            output.write(f"  dump: {record.planned_dump_file}")
        if record.planned_schema_file:
            output.write(f"  schema: {record.planned_schema_file}")
        output.write(f"  verified: {record.verified} (preview_only={record.preview_only})")
    output.write("")


def print_report_preview(report: BackupReportPreview) -> None:
    output.write(format_report_preview_markdown(report))


def print_verification_result(result: BackupVerificationResult) -> None:
    output.heading("BACKUP VERIFICATION")
    output.field("database", result.database)
    output.field("backup_kind", result.backup_kind)
    output.field("backup_id", result.backup_id)
    output.field("manifest_path", result.manifest_path)
    output.field("verified", result.verified)
    output.field("manifest_exists", result.manifest_exists)
    output.field("checksum_exists", result.checksum_exists)
    output.field("checksum_matches", result.checksum_matches)
    output.field("size_ok", result.size_ok)
    output.field("role_ok", result.role_ok)
    if result.checked_at:
        output.field("checked_at", result.checked_at)
    if result.issues:
        output.heading("Issues")
        for issue in result.issues:
            output.bullet(issue)
    if result.verified:
        output.write()
        output.write("Verification passed. Database backup artifacts are consistent.")
    else:
        output.write()
        output.write("Verification failed. Backup is not considered protected.")
