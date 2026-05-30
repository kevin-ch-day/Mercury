"""Display batch backup results."""

from mercury import output
from mercury.backup.batch import BackupBatchResult


def print_backup_batch_result(batch: BackupBatchResult) -> None:
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
            output.write(f"  refusal_reason: {result.refusal_reason}")
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
