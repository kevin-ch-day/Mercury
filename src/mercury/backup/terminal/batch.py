"""Display batch backup results."""

from mercury import output
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.terminal.format import format_backup_id_display, format_bytes
from mercury.core.execution_policy import backup_mode_label, load_execution_policy
from mercury.backup.batch_runner import (
    BackupBatchResult,
    FullBackupOutcome,
    FullBackupRunResult,
    small_production_backup_warning,
)
from mercury.backup.menu_options import ACTION_VERIFY, backup_menu_hint


def print_batch_small_backup_warnings(batch: BackupBatchResult) -> None:
    """Surface unexpectedly small newly written production dumps after a batch write."""
    for result in batch.results:
        warning = small_production_backup_warning(result)
        if warning:
            display_screen.write_status("warn", warning)


def _write_refusal_line(refusal: str) -> None:
    if refusal.startswith("Result:"):
        display_screen.write_summary(refusal)
        return
    display_screen.write_status("warn", refusal)


def _result_label(result) -> str:
    if result.executed:
        return "written"
    if result.refused:
        return "refused"
    return "planned"


def _batch_mode_label(batch: BackupBatchResult) -> str:
    if batch.execute:
        policy = load_execution_policy()
        return backup_mode_label(policy) if batch.executed_count else "preview blocked or refused"
    return "preview only"


def print_backup_batch_result(
    batch: BackupBatchResult,
    *,
    compact: bool = False,
    menu: bool = False,
    databases_label: str = "Production databases selected",
    suggest_verify: bool = False,
) -> None:
    if compact and menu:
        display_screen.write_fields(
            {
                "Backup mode": _batch_mode_label(batch),
                databases_label: len(batch.sources),
                "Written": batch.executed_count,
                "Preview": batch.dry_run_count,
                "Refused": batch.refused_count,
            }
        )
        if batch.results:
            rows = [
                [
                    result.database,
                    _result_label(result),
                    format_backup_id_display(result.manifest.backup_id, max_len=40)
                    if result.manifest
                    else "-",
                ]
                for result in batch.results
            ]
            display_screen.write_blank()
            display_screen.write_structured_table(
                Table.from_headers(
                    ["DATABASE", "RESULT", "BACKUP ID"],
                    rows,
                    style=TableStyle(indent=0),
                    min_col_widths=[28, 8, 18],
                    max_col_widths=[36, 10, 40],
                )
            )
            display_screen.write_blank()
            display_screen.write_summary("Complete backup IDs:")
            for result in batch.results:
                if result.manifest:
                    path = result.backup_directory_path or "-"
                    display_screen.write_hint(f"{result.manifest.backup_id} · {path}")
        refusal = next((r.refusal_reason for r in batch.results if r.refusal_reason), None)
        if refusal:
            display_screen.write_blank()
            _write_refusal_line(refusal)
        for error in batch.errors:
            display_screen.write_status("fail", error)
        if batch.executed_count and suggest_verify:
            display_screen.write_blank()
            display_screen.write_summary(
                f"Backups written. Next: {backup_menu_hint(ACTION_VERIFY)}."
            )
        elif batch.dry_run_count:
            display_screen.write_blank()
            display_screen.write_summary("Preview only; no files were written.")
        elif batch.refused_count and batch.execute:
            display_screen.write_blank()
            display_screen.write_summary("No backups written. Check storage/config or missing sources.")
        return

    if compact:
        names = [result.database for result in batch.results]
        display_screen.write_fields(
            {
                "Backup mode": _batch_mode_label(batch),
                databases_label: len(names),
            }
        )
        if names:
            display_screen.write_blank()
            display_screen.write_table(["DATABASE"], [[name] for name in names])
        refusal = next((r.refusal_reason for r in batch.results if r.refusal_reason), None)
        if refusal:
            _write_refusal_line(refusal)
        for error in batch.errors:
            display_screen.write_status("fail", error)
        if batch.executed_count:
            display_screen.write_status("ok", f"executed {batch.executed_count} backup(s)")
        return

    output.heading("BACKUP BATCH")
    output.field("backup_kind", batch.backup_kind)
    output.field("execute", batch.execute)
    output.field("sources", len(batch.sources))
    output.field("executed", batch.executed_count)
    output.field("dry_run", batch.dry_run_count)
    output.field("refused", batch.refused_count)

    for result in batch.results:
        output.write()
        output.write(f"- {result.database} [{result.backup_kind}]")
        output.write(f"  executed: {result.executed}")
        output.write(f"  dry_run: {result.dry_run}")
        if result.refusal_reason:
            label = "refusal_reason" if result.refused else "execution_note"
            output.write(f"  {label}: {result.refusal_reason}")
        if result.manifest:
            output.write(f"  verified: {result.manifest.verified}")
            output.write(f"  backup_id: {result.manifest.backup_id}")

    if batch.errors:
        output.heading("Errors")
        for error in batch.errors:
            output.bullet(error)

    if batch.execute and batch.refused_count and not batch.executed_count:
        output.write()
        output.write("Batch refused: check operator backup root, config/local.toml, or missing sources.")


def print_full_backup_run_result(result: FullBackupRunResult) -> None:
    """Combined summary for Backup Operations option [2]."""
    display_screen.write_blank()
    display_screen.write_summary("Full Backup Result")
    output.rule()
    display_screen.write_fields({"Run ID": result.run_id, "Result": result.outcome.value})
    display_screen.write_blank()
    display_screen.write_summary("PRODUCTION")
    display_screen.write_fields(
        {
            "Selected": result.production.selected,
            "Written": result.production.written,
            "Verified": result.production.verified,
            "Failed": result.production.failed,
            "Total size": format_bytes(result.production.total_size_bytes),
        }
    )
    for backup_id in result.production.backup_ids:
        display_screen.write_hint(f"backup_id: {backup_id}")
    for path in result.production.verification_evidence_paths:
        display_screen.write_hint(f"verify evidence: {path}")
    for warning in result.production.small_backup_warnings:
        display_screen.write_status("warn", warning)
    for issue in result.production.issues:
        display_screen.write_status("fail", issue)

    display_screen.write_blank()
    display_screen.write_summary("DEVELOPMENT")
    display_screen.write_fields(
        {
            "Requested": "Yes" if result.development.requested else "No",
            "Selected": result.development.selected,
            "Written": result.development.written,
            "Verified": result.development.verified,
            "Failed": result.development.failed,
            "Total size": format_bytes(result.development.total_size_bytes),
        }
    )
    if result.development.requested:
        display_screen.write_summary(
            "Development backups were created and verified for optional migration recovery. "
            "They are not included in the default production handoff bundle unless explicitly selected."
            if result.development.verified and result.development.failed == 0
            else "Development backup lane was requested; review failures before relying on it."
        )
        for backup_id in result.development.backup_ids:
            display_screen.write_hint(f"backup_id: {backup_id}")
        for path in result.development.verification_evidence_paths:
            display_screen.write_hint(f"verify evidence: {path}")
        for issue in result.development.issues:
            display_screen.write_status("fail", issue)

    display_screen.write_blank()
    display_screen.write_summary("OVERALL")
    display_screen.write_fields(
        {
            "Backups written": result.overall_written,
            "Verified": result.overall_verified,
            "Failed": result.overall_failed,
            "Artifacts": result.backup_artifacts_result.value,
            "Verification": result.verification_result.value,
            "Run evidence": result.run_evidence_result.value,
            "Result": result.outcome.value,
            "Package class": result.package_classification,
        }
    )
    if result.receipt_path:
        display_screen.write_hint(f"Run receipt: {result.receipt_path}")
    if result.receipt_sha256:
        display_screen.write_hint(f"Run receipt SHA-256: {result.receipt_sha256}")
    display_screen.write_hint(result.phase3b_separation_note)

    if result.outcome == FullBackupOutcome.PASS and result.next_actions:
        display_screen.write_blank()
        display_screen.write_summary("Next:")
        for action in result.next_actions:
            display_screen.write_hint(action)
    elif result.outcome != FullBackupOutcome.PASS:
        display_screen.write_blank()
        display_screen.write_summary(
            "Handoff backup set is not ready — resolve failures before restore-check or bundle write."
        )
        for action in result.next_actions:
            display_screen.write_hint(action)
