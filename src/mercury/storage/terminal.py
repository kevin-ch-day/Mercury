"""Terminal output for observe-only storage status and migration plans."""

from __future__ import annotations

from mercury.storage.migrate_plan import MigrationPlanReport
from mercury.storage.migrate_run import MigrationRunResult
from mercury.storage.migrate_verify import MigrationVerifyReport
from mercury.storage.cutover_readiness import CutoverReadinessReport
from mercury.storage.migrate_quarantine import QuarantineResult
from mercury.storage.audit import StorageAuditReport
from mercury.storage.cutover_plan import CutoverPlan
from mercury.storage.report import StorageStatusReport, suggested_primary_fstab_line
from mercury.terminal import screen as display_screen


def print_storage_status(report: StorageStatusReport) -> None:
    display_screen.open_screen("Mercury Storage")
    display_screen.write_fields(
        {
            "Active write role": report.active_write_role.value,
            "Migration state": report.migration_state.value,
            "Cutover complete": "yes" if report.config.cutover_complete else "no",
            "Schema version": report.config.schema_version,
        }
    )
    display_screen.write_blank()
    display_screen.write_section("Configured roots")
    for root in (report.primary, report.legacy):
        kind = "ok" if root.validation.ok else "warn" if root.status_tag == "[--]" else "fail"
        active = " · ACTIVE WRITER" if root.is_active_writer else ""
        if root.validation.ok:
            detail = "mounted" + (
                " and writable" if root.writable_policy else " (read-only policy)"
            )
        else:
            detail = root.validation.blocker or root.validation.code.value
        display_screen.write_status(
            kind,
            f"{root.label} ({root.role}) @ {root.mount_path} — {detail}{active}",
        )
        display_screen.write_fields(
            {
                "  UUID": root.filesystem_uuid,
                "  Policy writable": "yes" if root.writable_policy else "no",
                "  Mount mode": root.physical_mount_mode,
                "  Validation": root.validation.code.value,
            }
        )
        if root.validation.space is not None:
            display_screen.write_fields({"  Space": root.validation.space.summary()})
        if root.validation.identity.stale_mountpoint_entries:
            display_screen.write_hint(
                "Stale mount-point entries (not removed): "
                + ", ".join(root.validation.identity.stale_mountpoint_entries)
            )
    warnings = report.warning_lines()
    if warnings:
        display_screen.write_blank()
        display_screen.write_section("Warnings")
        for warning in warnings:
            display_screen.write_status("warn", warning)
    display_screen.write_blank()
    display_screen.write_section("Operator notes")
    if report.config.cutover_complete:
        display_screen.write_hint("Routine backups and migration artifacts write to the canonical HDD. USB is recovery archive only.")
        display_screen.write_hint("Package status: ./run.sh migration package-status")
        display_screen.write_hint("USB archive receipt: ./run.sh storage archive-receipt")
        display_screen.write_hint("Destination readiness: ./run.sh migration next")
    else:
        display_screen.write_hint("Routine backups still target the active write role (legacy until cutover).")
    display_screen.write_hint(
        f"Primary fstab draft (not applied by Mercury): {suggested_primary_fstab_line(report.config)}"
    )
    display_screen.write_hint("Validate mounts: ./run.sh storage validate")
    if not report.config.cutover_complete:
        display_screen.write_hint("Dry-run migration inventory: ./run.sh storage migrate-plan")
        display_screen.write_hint("Preview copy: ./run.sh storage migrate-run")
        display_screen.write_hint("Verify copy: ./run.sh storage migrate-verify")
        display_screen.write_hint("Conflict quarantine: ./run.sh storage migrate-quarantine")
        display_screen.write_hint("Cutover checklist: ./run.sh storage cutover-readiness")
        display_screen.write_hint(f"Suggested next: ./run.sh {report.next_step()}")


def print_storage_validate(report: StorageStatusReport) -> int:
    """Print validation details. Returns process exit code (0=active writer OK)."""
    print_storage_status(report)
    active = report.primary if report.active_write_role.value == "primary" else report.legacy
    if active.validation.ok:
        display_screen.write_summary("Active write root passed mount validation.")
    return 0


def print_storage_audit(report: StorageAuditReport) -> int:
    """Print the configured-root audit with post-cutover historical semantics."""
    display_screen.open_screen("Mercury Storage Audit")
    metadata_label = "Historical USB comparison" if report.post_cutover else "Metadata verification"
    metadata_value = report.verification.summary_line()
    if report.post_cutover and report.verification.mismatches:
        metadata_value = (
            f"archive drift observed · matched={report.verification.matched} "
            f"differences={len(report.verification.mismatches)}"
        )
    display_screen.write_fields(
        {
            "Source (legacy)": report.verification.source_mount,
            "Destination (primary)": report.verification.dest_mount,
            metadata_label: metadata_value,
            "SHA-256 requested": "yes" if report.hashes_requested else "no",
        }
    )
    if report.hashes_requested and report.verification.ok:
        display_screen.write_blank()
        display_screen.write_section("SHA-256 comparison")
        display_screen.write_fields(
            {
                "Files hashed": str(report.files_hashed),
                "Identical": str(report.identical_hashes),
                "Durable differences": str(len(report.durable_differences)),
                "Ephemeral differences": str(len(report.ephemeral_differences)),
            }
        )
    audit_warnings = (*report.verification.warnings, *report.warnings)
    if audit_warnings:
        display_screen.write_blank()
        display_screen.write_section("Warnings")
        for warning in audit_warnings:
            display_screen.write_status("warn", warning)
    if report.differences:
        display_screen.write_blank()
        display_screen.write_section("Differences (first 20)")
        for item in report.differences[:20]:
            kind = "warn" if item.ephemeral else "fail"
            suffix = " (ephemeral writer drift)" if item.ephemeral else ""
            display_screen.write_status(kind, f"{item.relative_path} — {item.issue}{suffix}")
    display_screen.write_blank()
    if report.exit_code == 0:
        detail = "Byte-level audit passed." if report.hashes_requested else "Metadata audit passed."
        display_screen.write_summary(f"{detail} " + ("HDD remains the active writer." if report.config.cutover_complete else "Writers still remain on legacy until cutover."))
        return 0
    if report.exit_code == 1:
        display_screen.write_summary(
            "Audit completed with warnings. "
            + (
                "HDD remains the active writer; USB comparison is historical evidence only."
                if report.post_cutover
                else "Writers still remain on legacy until cutover."
            )
        )
        return 1
    if report.post_cutover:
        display_screen.write_summary(
            "Active HDD validation failed: "
            + "; ".join(report.post_cutover_blockers)
            + ". Repair the HDD mount before relying on Mercury."
        )
        return 2
    display_screen.write_summary("Audit found durable differences or verification blockers — do not cut over.")
    return 2
    display_screen.write_status(
        "fail",
        f"Active write root failed validation: {active.validation.blocker}",
    )
    return 1


def print_migration_plan(report: MigrationPlanReport, *, report_path: str | None = None) -> int:
    """Print migrate-plan results. Returns 0 when plan is ready (still dry-run)."""
    display_screen.open_screen("Mercury Storage Migration Plan")
    display_screen.write_fields(
        {
            "Source (legacy)": report.source_mount,
            "Destination (primary)": report.dest_mount,
            "Conflict policy": report.conflict_policy,
            "Migration state": report.migration_state,
            "Active write role": report.active_write_role,
            "Summary": report.summary_line(),
        }
    )
    display_screen.write_blank()
    display_screen.write_section("Inventory")
    display_screen.write_fields(
        {
            "Source files": str(report.source_file_count),
            "Copy files": str(report.copy_file_count),
            "Copy bytes": f"{report.copy_bytes} ({report.copy_bytes / (1024**3):.3f} GiB)",
            "Mkdir": str(report.mkdir_count),
            "Symlinks": str(report.link_count),
            "Identical (skip)": str(report.skip_identical_count),
            "Conflicts": str(report.conflict_count),
            "Conflict bytes": str(report.conflict_bytes),
            "Ephemeral refresh": str(report.refresh_ephemeral_count),
            "Excluded": str(report.skip_excluded_count),
        }
    )
    if report.space is not None:
        display_screen.write_fields({"Primary space": report.space.summary()})

    if report.blockers:
        display_screen.write_blank()
        display_screen.write_section("Blockers")
        for blocker in report.blockers:
            display_screen.write_status("fail", blocker)

    conflicts = report.conflict_entries()
    if conflicts:
        display_screen.write_blank()
        display_screen.write_section("Conflicts (first 20)")
        for entry in conflicts[:20]:
            display_screen.write_status(
                "fail",
                f"{entry.relative_path} — {entry.detail or entry.action}",
            )
        if len(conflicts) > 20:
            display_screen.write_hint(f"… {len(conflicts) - 20} more conflict(s)")

    if report.warnings:
        display_screen.write_blank()
        display_screen.write_section("Notes")
        for warning in report.warnings:
            display_screen.write_status("warn", warning)

    if report_path:
        display_screen.write_hint(f"Wrote plan report: {report_path}")

    display_screen.write_blank()
    if report.ready_for_migrate_execute:
        display_screen.write_summary(
            "Plan is clear. No files were copied. Next: ./run.sh storage migrate-run"
        )
        return 0
    display_screen.write_summary("Plan blocked — resolve blockers before any migrate execute.")
    if report.conflict_count:
        display_screen.write_hint(
            "Primary conflicts: ./run.sh storage migrate-quarantine  (then re-run migrate-plan)"
        )
    return 1


def print_migration_run(result: MigrationRunResult) -> int:
    """Print migrate-run outcome. Returns 0 on ok dry-run or successful execute."""
    display_screen.open_screen("Mercury Storage Migration Run")
    display_screen.write_fields(
        {
            "Mode": "dry-run" if result.dry_run else ("execute" if result.executed else "refused"),
            "Source (legacy)": result.source_mount,
            "Destination (primary)": result.dest_mount,
            "Summary": result.summary_line(),
            "Confirmation phrase": result.confirmation_phrase,
        }
    )
    display_screen.write_blank()
    display_screen.write_section("Counts")
    display_screen.write_fields(
        {
            "Files copied": str(result.copied_files),
            "Dirs created": str(result.created_dirs),
            "Symlinks": str(result.created_links),
            "Identical skipped": str(result.skipped_identical),
            "Ephemeral refreshed": str(result.refreshed_ephemeral),
            "Resume skipped": str(result.resumed_skipped),
            "Bytes": str(result.bytes_copied),
        }
    )
    if result.blockers:
        display_screen.write_blank()
        display_screen.write_section("Blockers")
        for blocker in result.blockers:
            display_screen.write_status("fail", blocker)
    if result.errors:
        display_screen.write_blank()
        display_screen.write_section("Errors")
        for err in result.errors:
            display_screen.write_status("fail", err)
    if result.warnings:
        display_screen.write_blank()
        display_screen.write_section("Notes")
        for warning in result.warnings:
            display_screen.write_status("warn", warning)
    if result.control_report_path:
        display_screen.write_hint(f"Primary control report: {result.control_report_path}")
    display_screen.write_blank()
    if result.dry_run and result.ok:
        display_screen.write_summary(
            "Dry-run ok. Writers still on legacy. Live copy: "
            "./run.sh storage migrate-run --execute  (type MIGRATE PRIMARY)"
        )
        return 0
    if result.executed and result.ok:
        display_screen.write_summary(
            "Copy complete. Writers still on legacy. Next: ./run.sh storage migrate-verify"
        )
        return 0
    display_screen.write_summary("Migration run blocked or incomplete.")
    return 1


def print_migration_verify(report: MigrationVerifyReport) -> int:
    """Print migrate-verify outcome. Returns 0 when verified."""
    display_screen.open_screen("Mercury Storage Migration Verify")
    display_screen.write_fields(
        {
            "Source (legacy)": report.source_mount,
            "Destination (primary)": report.dest_mount,
            "Summary": report.summary_line(),
        }
    )
    display_screen.write_blank()
    display_screen.write_section("Checks")
    display_screen.write_fields(
        {
            "Files": str(report.checked_files),
            "Dirs": str(report.checked_dirs),
            "Symlinks": str(report.checked_links),
            "Matched": str(report.matched),
            "Mismatches": str(len(report.mismatches)),
        }
    )
    if report.blockers:
        display_screen.write_blank()
        display_screen.write_section("Blockers")
        for blocker in report.blockers:
            display_screen.write_status("fail", blocker)
    if report.mismatches:
        display_screen.write_blank()
        display_screen.write_section("Mismatches (first 20)")
        for item in report.mismatches[:20]:
            display_screen.write_status(
                "fail",
                f"{item.relative_path} — {item.issue}"
                + (f" ({item.detail})" if item.detail else ""),
            )
        if len(report.mismatches) > 20:
            display_screen.write_hint(f"… {len(report.mismatches) - 20} more")
    if report.warnings:
        display_screen.write_blank()
        display_screen.write_section("Notes")
        for warning in report.warnings:
            display_screen.write_status("warn", warning)
    display_screen.write_blank()
    if report.ok:
        display_screen.write_summary(
            "Migration verified. Writers still on legacy. Cutover is a separate approve step."
        )
        return 0
    display_screen.write_summary("Verification failed — do not cut over.")
    return 1


def print_quarantine_result(result: QuarantineResult) -> int:
    display_screen.open_screen("Mercury Storage Conflict Quarantine")
    display_screen.write_fields(
        {
            "Mode": "dry-run" if result.dry_run else ("execute" if result.executed else "refused"),
            "Primary": result.dest_mount,
            "Quarantine root": result.quarantine_root or "—",
            "Summary": result.summary_line(),
            "Confirmation phrase": result.confirmation_phrase,
        }
    )
    if result.quarantined:
        display_screen.write_blank()
        display_screen.write_section("Paths")
        for rel in result.quarantined[:30]:
            display_screen.write_status("warn" if result.dry_run else "ok", rel)
        if len(result.quarantined) > 30:
            display_screen.write_hint(f"… {len(result.quarantined) - 30} more")
    if result.blockers:
        display_screen.write_blank()
        display_screen.write_section("Blockers")
        for blocker in result.blockers:
            display_screen.write_status("fail", blocker)
    if result.warnings:
        display_screen.write_blank()
        display_screen.write_section("Notes")
        for warning in result.warnings:
            display_screen.write_status("warn", warning)
    display_screen.write_blank()
    if result.ok and result.dry_run:
        display_screen.write_summary(
            "Dry-run ok. Live: ./run.sh storage migrate-quarantine --execute"
        )
        return 0
    if result.ok and result.executed:
        display_screen.write_summary(
            "Conflicts moved aside on primary. Next: ./run.sh storage migrate-plan"
        )
        return 0
    if result.ok and not result.quarantined:
        display_screen.write_summary("No conflicts to quarantine.")
        return 0
    display_screen.write_summary("Quarantine blocked or incomplete.")
    return 1


def print_cutover_readiness(report: CutoverReadinessReport) -> int:
    """Print read-only cutover checklist. Returns 0 when ready (still no cutover)."""
    display_screen.open_screen("Mercury Cutover Readiness")
    display_screen.write_fields(
        {
            "Cutover status": "complete" if report.migration_state == "cutover_complete" else ("ready" if report.ready else "blocked"),
            "Active write role": report.active_write_role,
            "Migration state": report.migration_state,
            "Fstab draft (not applied)": report.fstab_draft,
        }
    )
    display_screen.write_blank()
    display_screen.write_section("Checks")
    for check in report.checks:
        kind = "ok" if check.ok else "fail"
        display_screen.write_status(kind, f"{check.key}: {check.detail}")
    if report.blockers:
        display_screen.write_blank()
        display_screen.write_section("Blockers")
        for blocker in report.blockers:
            display_screen.write_status("fail", blocker)
    if report.warnings:
        display_screen.write_blank()
        display_screen.write_section("Notes")
        for warning in report.warnings:
            display_screen.write_status("warn", warning)
    display_screen.write_blank()
    if report.migration_state == "cutover_complete":
        display_screen.write_summary("HDD is the active writer; USB is retained as recovery archive.")
        return 0
    if report.ready:
        display_screen.write_summary(
            "Checklist passed. Cutover approve (switch writers / remount) is not enabled yet."
        )
        return 0
    display_screen.write_summary("Not ready for cutover — resolve blockers first.")
    return 1


def print_cutover_plan(plan: CutoverPlan) -> int:
    """Render the future writer switch without offering an unsafe execute path."""
    display_screen.open_screen("Mercury Writer Cutover Plan")
    if plan.already_complete:
        display_screen.write_fields(
            {
                "Cutover status": "complete",
                "Active writer": plan.readiness.active_write_role,
                "USB role": "recovery archive",
                "Execution available": "not applicable",
            }
        )
        display_screen.write_blank()
        display_screen.write_summary("HDD writer cutover is already complete. No writer switch is offered.")
        display_screen.write_hint("Review active evidence: ./run.sh migration package-status")
        return 0
    display_screen.write_fields(
        {
            "Storage mirror ready": "yes" if plan.readiness.ready else "no",
            "Ready to switch writers": "yes" if plan.ready_for_future_execution else "no",
            "Current writer": plan.readiness.active_write_role,
            "Target writer": plan.target_active_write_role,
            "Execution available": "no",
        }
    )
    display_screen.write_blank()
    display_screen.write_section("Coordinated path changes")
    for change in plan.path_changes:
        display_screen.write_fields({change.key: f"{change.legacy_path} → {change.primary_path}"})
    display_screen.write_blank()
    display_screen.write_status(
        "warn",
        "Plan only: Mercury does not yet switch writers, edit fstab, or delete USB data.",
    )
    for blocker in plan.runtime_blockers:
        display_screen.write_status("warn", blocker)
    return 0 if plan.ready_for_future_execution else 1
