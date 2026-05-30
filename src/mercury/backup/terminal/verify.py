"""Plain-text display for verification plan, backup list, and report preview."""

from __future__ import annotations

from pydantic import BaseModel, Field

from mercury import output
from mercury.terminal import format as display_format
from mercury.terminal import screen as display_screen
from mercury.backup.on_disk_index import DemoBackupList, OnDiskBackupList
from mercury.reporting.preview import BackupReportPreview, format_report_preview_markdown
from mercury.backup.verification import BackupVerificationResult, VerificationPlan


class VerifyMenuSummary(BaseModel):
    verified: int = 0
    missing: int = 0
    failed: int = 0
    rows: list[list[str]] = Field(default_factory=list)


def run_verify_all_for_menu(*, update_manifest: bool = False) -> VerifyMenuSummary:
    """Verify all production backup sources for menu options 3 and 5."""
    from mercury.backup.batch_runner import resolve_batch_sources
    from mercury.backup.find_latest_backup import find_latest_backup_directory
    from mercury.core.execution_policy import load_execution_policy
    from mercury.core.runtime import should_probe_database_status
    from mercury.backup.verification import verify_backup_directory

    policy = load_execution_policy()
    sources = resolve_batch_sources(live=should_probe_database_status())
    summary = VerifyMenuSummary()
    for database in sources:
        backup_dir = find_latest_backup_directory(policy.backup_root, database)
        if backup_dir is None:
            summary.missing += 1
            summary.rows.append([database, "missing"])
            continue
        result = verify_backup_directory(
            backup_dir,
            database=database,
            update_manifest=update_manifest,
        )
        if result.verified:
            summary.verified += 1
            summary.rows.append([database, "verified"])
        else:
            summary.failed += 1
            issue = result.issues[0] if result.issues else "failed"
            summary.rows.append([database, issue[:40]])
    return summary


def print_verify_menu_summary(summary: VerifyMenuSummary) -> None:
    display_screen.write_fields(
        {
            "verified": summary.verified,
            "missing": summary.missing,
            "failed": summary.failed,
        }
    )
    if summary.rows:
        display_screen.write_blank()
        display_screen.write_table(
            ["DATABASE", "STATUS"],
            summary.rows,
            max_col_widths=[36, 24],
        )
    elif summary.missing == 0 and summary.failed == 0 and summary.verified == 0:
        display_screen.write_status("warn", "No backup sources configured.")


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


def print_on_disk_backup_list(
    backup_list: OnDiskBackupList,
    *,
    compact: bool = False,
    menu: bool = False,
) -> None:
    if compact and menu:
        display_screen.write_fields({"backup_root": str(backup_list.backup_root), "count": len(backup_list.records)})
        if not backup_list.records:
            display_screen.write_status("warn", "No backups on disk yet.")
            return
        rows = []
        for record in backup_list.records:
            status = display_format.format_verification_status(verified=record.verified)
            rows.append([record.database, record.backup_kind, status])
        display_screen.write_table(["DATABASE", "KIND", "STATUS"], rows, max_col_widths=[36, 12, 12])
        return

    if compact:
        display_screen.write_fields({"backups": len(backup_list.records), "root": str(backup_list.backup_root)})
        if not backup_list.records:
            display_screen.write_status("warn", f"none under {backup_list.backup_root}")
            return
        rows = []
        for record in backup_list.records:
            status = display_format.format_verification_status(verified=record.verified)
            rows.append([record.database, record.backup_kind, status])
        display_screen.write_table(["DATABASE", "KIND", "STATUS"], rows)
        return

    for line in display_format.format_report_header("BACKUP LIST (on-disk)"):
        output.write(line)
    output.write(f"backup_root: {backup_list.backup_root}")
    output.write(f"Note: {backup_list.note}")
    output.write("")
    for record in backup_list.records:
        output.write(f"- {record.database} [{record.backup_kind}]")
        output.write(f"  backup_id: {record.backup_id}")
        output.write(f"  directory: {record.directory}")
        if record.dump_file:
            output.write(f"  dump: {record.dump_file}")
        if record.schema_file:
            output.write(f"  schema: {record.schema_file}")
        output.write(f"  verified: {record.verified}")
        if record.created_at:
            output.write(f"  created_at: {record.created_at}")
    output.write("")


def print_report_preview(report: BackupReportPreview) -> None:
    output.write(format_report_preview_markdown(report))


def print_verification_result(result: BackupVerificationResult, *, compact: bool = False) -> None:
    if compact:
        if result.verified:
            display_screen.write_status("ok", f"{result.database}: verified")
        else:
            detail = result.issues[0] if result.issues else "failed"
            display_screen.write_status("fail", f"{result.database}: {detail}")
        return

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
