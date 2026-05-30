"""Display batch backup results."""

from mercury import output
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import BackupBatchResult


def print_backup_batch_result(
    batch: BackupBatchResult,
    *,
    compact: bool = False,
    menu: bool = False,
) -> None:
    if compact and menu:
        names = [result.database for result in batch.results]
        mode = "live" if batch.execute else "dry-run"
        display_screen.write_fields(
            {
                "mode": mode,
                "sources": len(names),
                "databases": ", ".join(names) if names else "(none)",
            }
        )
        refusal = next((r.refusal_reason for r in batch.results if r.refusal_reason), None)
        if refusal:
            display_screen.write_status("warn", refusal)
        for error in batch.errors:
            display_screen.write_status("fail", error)
        return

    if compact:
        names = [result.database for result in batch.results]
        mode = "live" if batch.execute else "dry-run"
        display_screen.write_fields(
            {
                "mode": mode,
                "sources": len(names),
            }
        )
        if names:
            display_screen.write_blank()
            display_screen.write_table(["DATABASE"], [[name] for name in names])
        refusal = next((r.refusal_reason for r in batch.results if r.refusal_reason), None)
        if refusal:
            display_screen.write_status("warn", refusal)
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
        output.write("Batch refused: enable live execution in config/local.toml or env.")
