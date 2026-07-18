"""Mercury multi-root storage (status/validate/migrate-*; no cutover yet)."""

from mercury.storage.cutover_readiness import (
    CutoverReadinessReport,
    build_cutover_readiness,
)
from mercury.storage.migrate_plan import MigrationPlanReport, build_migration_plan
from mercury.storage.migrate_quarantine import QuarantineResult, quarantine_migration_conflicts
from mercury.storage.migrate_run import MigrationRunResult, run_migration
from mercury.storage.migrate_verify import MigrationVerifyReport, verify_migration
from mercury.storage.report import (
    StorageStatusReport,
    build_storage_status_report,
    suggested_primary_fstab_line,
)

__all__ = [
    "CutoverReadinessReport",
    "MigrationPlanReport",
    "MigrationRunResult",
    "MigrationVerifyReport",
    "QuarantineResult",
    "StorageStatusReport",
    "build_cutover_readiness",
    "build_migration_plan",
    "build_storage_status_report",
    "quarantine_migration_conflicts",
    "run_migration",
    "suggested_primary_fstab_line",
    "verify_migration",
]
