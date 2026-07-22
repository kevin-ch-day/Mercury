"""Backup planning, execution, verification, and display."""

from mercury.backup.bundle import DatabaseBundleEntry, DatabaseBundlePlan, build_database_bundle_plan, write_database_bundle_plan
from mercury.backup.terminal.plan import print_backup_plan
from mercury.backup.status import BackupStatusEntry, BackupStatusReport, build_backup_status_report
from mercury.backup.backup_runner import (
    BackupExecutionError,
    BackupExecutionResult,
    assert_not_production_restore_target,
    assert_safe_backup_source,
    execute_backup,
    plan_backup_execution,
)
from mercury.backup.terminal.runner import print_backup_execution
from mercury.backup.layout import (
    CHECKSUM_FILENAME,
    MANIFEST_FILENAME,
    REPORT_FILENAME,
    TOOL_FAMILY,
    BackupLayoutPaths,
    build_backup_layout,
    list_standard_filenames,
    planned_backup_directory,
    planned_dump_filename,
)
from mercury.backup.on_disk_index import DEMO_BACKUP_RECORDS, DemoBackupList, build_demo_backup_list
from mercury.backup.manifest import (
    BACKUP_KIND_LABELS,
    BackupKind,
    BackupManifest,
    build_backup_manifest,
    planned_backup_dir,
    planned_backup_files,
)
from mercury.backup.manifest_preview import (
    ManifestPreview,
    ManifestPreviewError,
    build_manifest_preview,
    format_manifest_preview_json,
)
from mercury.backup.sample_manifest import write_sample_manifests
from mercury.backup.schema_plan import SchemaBackupPlanDryRun, build_schema_backup_plan_demo
from mercury.backup.verification import (
    BackupVerificationResult,
    VerificationPlan,
    apply_verification_success,
    build_demo_verification_result,
    build_verification_plan_demo,
    verify_backup_artifacts,
    verify_backup_directory,
)
from mercury.backup.terminal.verify import (
    print_demo_backup_list,
    print_verification_plan,
)
from mercury.backup.terminal.status import print_backup_status_report
from mercury.backup.terminal.bundle import print_database_bundle_plan

__all__ = [
    "BACKUP_KIND_LABELS",
    "DatabaseBundleEntry",
    "DatabaseBundlePlan",
    "BackupExecutionError",
    "BackupExecutionResult",
    "BackupKind",
    "BackupLayoutPaths",
    "BackupManifest",
    "BackupStatusEntry",
    "BackupStatusReport",
    "BackupVerificationResult",
    "CHECKSUM_FILENAME",
    "DEMO_BACKUP_RECORDS",
    "DemoBackupList",
    "MANIFEST_FILENAME",
    "ManifestPreview",
    "ManifestPreviewError",
    "REPORT_FILENAME",
    "SchemaBackupPlanDryRun",
    "TOOL_FAMILY",
    "VerificationPlan",
    "apply_verification_success",
    "assert_not_production_restore_target",
    "assert_safe_backup_source",
    "build_backup_layout",
    "build_database_bundle_plan",
    "build_backup_status_report",
    "build_demo_backup_list",
    "build_demo_verification_result",
    "build_manifest_preview",
    "build_schema_backup_plan_demo",
    "build_verification_plan_demo",
    "execute_backup",
    "format_manifest_preview_json",
    "list_standard_filenames",
    "plan_backup_execution",
    "planned_backup_dir",
    "planned_backup_directory",
    "planned_backup_files",
    "planned_dump_filename",
    "print_backup_execution",
    "print_database_bundle_plan",
    "print_backup_plan",
    "print_backup_status_report",
    "print_demo_backup_list",
    "print_verification_plan",
    "verify_backup_artifacts",
    "verify_backup_directory",
    "write_database_bundle_plan",
    "write_sample_manifests",
]
