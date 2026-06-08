"""Display backup execution results."""

from mercury.backup.backup_runner import BackupExecutionResult
from mercury import output


def print_backup_execution(result: BackupExecutionResult) -> None:
    output.heading("BACKUP EXECUTION")
    output.field("database", result.database)
    output.field("backup_kind", result.backup_kind)
    output.field("dry_run", result.dry_run)
    output.field("executed", result.executed)
    output.field("refused", result.refused)
    output.field("live_actions_enabled", result.live_actions_enabled)
    if result.refusal_reason:
        output.field("refusal_reason", result.refusal_reason)
    output.field("backup_directory_relative", result.backup_directory)
    if result.backup_directory_path:
        output.field("backup_directory_path", result.backup_directory_path)
    if result.dump_file:
        output.field("dump_file", result.dump_file)
    if result.schema_file:
        output.field("schema_file", result.schema_file)
    output.field("tool_used", result.tool_used)
    output.field("command", result.command)
    if result.schema_command:
        output.field("schema_command", result.schema_command)
    if result.manifest_file:
        output.field("manifest_file", result.manifest_file)
    if result.checksum_file:
        output.field("checksum_file", result.checksum_file)

    output.heading("Safety notes")
    for note in result.safety_notes:
        output.bullet(note)

    if result.manifest:
        output.heading("Manifest")
        output.field("backup_id", result.manifest.backup_id)
        output.field("sha256", result.manifest.sha256)
        output.field("size_bytes", result.manifest.size_bytes)
        output.field("verified", result.manifest.verified)
