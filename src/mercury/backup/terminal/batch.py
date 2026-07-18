"""Display batch backup results."""

from mercury import output
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle
from mercury.core.execution_policy import backup_mode_label, load_execution_policy
from mercury.backup.batch_runner import BackupBatchResult


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
) -> None:
    if compact and menu:
        display_screen.write_fields(
            {
                "Backup mode": _batch_mode_label(batch),
                "Source databases": len(batch.sources),
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
                    result.manifest.backup_id if result.manifest else "-",
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
        refusal = next((r.refusal_reason for r in batch.results if r.refusal_reason), None)
        if refusal:
            display_screen.write_blank()
            _write_refusal_line(refusal)
        for error in batch.errors:
            display_screen.write_status("fail", error)
        if batch.executed_count:
            display_screen.write_blank()
            display_screen.write_summary("Backups written. Next: verify source backups [3].")
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
                "Source databases": len(names),
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
