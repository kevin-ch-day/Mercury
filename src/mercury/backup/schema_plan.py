"""Dry-run schema-only export plan for production / authority databases (M4)."""

from pydantic import BaseModel, Field

from mercury.database.backup_planning import ExcludedDatabase, build_demo_backup_plan
from mercury.core.safety import BACKUP_KIND_SCHEMA_ONLY

SCHEMA_PLAN_NOTES = [
    "Dry-run only.",
    "Schema-only means structure only: tables/views/routines/triggers/events, no table data.",
    "Future implementation should use mariadb-dump/mysqldump with no-data style options.",
    "Full backups are still required for disaster recovery and prod-to-dev sync.",
]


class SchemaBackupPlanDryRun(BaseModel):
    mode: str = "dry-run"
    backup_kind: str = BACKUP_KIND_SCHEMA_ONLY
    sources: list[str] = Field(default_factory=list)
    excluded: list[ExcludedDatabase] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def build_schema_backup_plan_demo() -> SchemaBackupPlanDryRun:
    """
    Schema-only plan from demo/catalog: *_prod + shared authority only.
    """
    full_plan = build_demo_backup_plan()
    return _schema_plan_from_backup_plan(full_plan)


def build_schema_backup_plan_live() -> SchemaBackupPlanDryRun:
    """Schema-only plan from live server inventory."""
    from mercury.database.discovery import discover
    from mercury.database.backup_planning import build_backup_plan_from_inventory

    full_plan = build_backup_plan_from_inventory(discover("live"))
    return _schema_plan_from_backup_plan(full_plan)


def _schema_plan_from_backup_plan(full_plan) -> SchemaBackupPlanDryRun:
    return SchemaBackupPlanDryRun(
        sources=list(full_plan.backup_sources),
        excluded=list(full_plan.excluded),
        notes=list(SCHEMA_PLAN_NOTES),
    )
