"""Display batch backup results."""

from __future__ import annotations

from mercury import output
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.terminal.format import format_bytes
from mercury.terminal.theme import hint_text
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


def _write_dense_lines(lines: list[str]) -> None:
    for line in lines:
        output.write(hint_text(line))


def print_backup_batch_result(
    batch: BackupBatchResult,
    *,
    compact: bool = False,
    menu: bool = False,
    databases_label: str = "Production databases selected",
    suggest_verify: bool = False,
) -> None:
    if compact and menu:
        selected_label = databases_label.replace(" selected", "").replace("Selected", "").strip()
        if not selected_label:
            selected_label = "databases"
        display_screen.write_summary(
            f"{_batch_mode_label(batch)} · {selected_label} {len(batch.sources)} · "
            f"written {batch.executed_count} · preview {batch.dry_run_count} · "
            f"refused {batch.refused_count}"
        )
        if batch.results:
            rows = [
                [
                    result.database,
                    _result_label(result),
                    result.manifest.backup_id if result.manifest else "-",
                ]
                for result in batch.results
            ]
            display_screen.write_structured_table(
                Table.from_headers(
                    ["DATABASE", "RESULT", "BACKUP ID"],
                    rows,
                    style=TableStyle(indent=0),
                    min_col_widths=[28, 8, 24],
                    max_col_widths=[36, 10, 72],
                )
            )
        refusal = next((r.refusal_reason for r in batch.results if r.refusal_reason), None)
        if refusal:
            _write_refusal_line(refusal)
        for error in batch.errors:
            display_screen.write_status("fail", error)
        if batch.executed_count and suggest_verify:
            display_screen.write_summary(
                f"Backups written. Next: {backup_menu_hint(ACTION_VERIFY)}."
            )
        elif batch.dry_run_count:
            display_screen.write_summary("Preview only; no files were written.")
        elif batch.refused_count and batch.execute:
            display_screen.write_summary(
                "No backups written. Check storage/config or missing sources."
            )
        return

    if compact:
        names = [result.database for result in batch.results]
        display_screen.write_summary(
            f"{_batch_mode_label(batch)} · {databases_label} {len(names)}"
        )
        if names:
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
        output.write(f"- {result.database} [{result.backup_kind}]")
        output.write(f"  executed: {result.executed}  dry_run: {result.dry_run}")
        if result.refusal_reason:
            label = "refusal_reason" if result.refused else "execution_note"
            output.write(f"  {label}: {result.refusal_reason}")
        if result.manifest:
            output.write(
                f"  verified: {result.manifest.verified}  backup_id: {result.manifest.backup_id}"
            )

    if batch.errors:
        output.heading("Errors")
        for error in batch.errors:
            output.bullet(error)

    if batch.execute and batch.refused_count and not batch.executed_count:
        output.write(
            "Batch refused: check operator backup root, config/local.toml, or missing sources."
        )


def print_full_backup_run_result(result: FullBackupRunResult) -> None:
    """Final full-backup summary — overall status only (lane tables already printed)."""
    display_screen.write_summary(
        f"Full Backup Result · {result.outcome.value} · {result.run_id}"
    )
    prod = (
        f"Prod: {result.production.written} written, {result.production.verified} verified, "
        f"{result.production.failed} failed, {format_bytes(result.production.total_size_bytes)}"
    )
    _write_dense_lines([prod])
    if result.development.requested:
        dev = (
            f"Dev: {result.development.written} written, {result.development.verified} verified, "
            f"{result.development.failed} failed, {format_bytes(result.development.total_size_bytes)} "
            "(optional recovery; not default handoff)"
        )
        _write_dense_lines([dev])
    overall = (
        f"Overall: artifacts {result.backup_artifacts_result.value} · "
        f"verification {result.verification_result.value} · "
        f"evidence {result.run_evidence_result.value} · "
        f"{result.package_classification}"
    )
    _write_dense_lines([overall])
    receipt_lines: list[str] = []
    if result.receipt_path:
        receipt_lines.append(f"Receipt: {result.receipt_path}")
    if result.receipt_sha256:
        receipt_lines.append(f"SHA-256: {result.receipt_sha256}")
    if receipt_lines:
        _write_dense_lines(receipt_lines)
    output.write(hint_text(result.phase3b_separation_note))

    if result.outcome == FullBackupOutcome.PASS and result.next_actions:
        display_screen.write_summary("Next: " + "; ".join(result.next_actions))
    elif result.outcome != FullBackupOutcome.PASS:
        display_screen.write_summary(
            "Handoff backup set is not ready — resolve failures before restore-check or bundle write."
        )
        if result.next_actions:
            _write_dense_lines(result.next_actions)

    for warning in result.production.small_backup_warnings:
        display_screen.write_status("warn", warning)
    for issue in [*result.production.issues, *result.development.issues]:
        display_screen.write_status("fail", issue)
