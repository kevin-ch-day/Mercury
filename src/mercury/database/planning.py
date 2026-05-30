"""Dry-run backup planning from database names or inventory."""

from pydantic import BaseModel, Field

from mercury.database.core import (
    PLATFORM_DATABASES,
    DatabaseClassification,
    DatabaseInventory,
    backup_source_names,
    classify_database,
    exclusion_reason,
)
from mercury.database.discovery import discover_from_config
from mercury.safety import SAFETY_NOTES

DEMO_DATABASES: list[str] = PLATFORM_DATABASES


class ExcludedDatabase(BaseModel):
    name: str
    role: str
    reason: str


class BackupPlanDryRun(BaseModel):
    mode: str = "dry-run"
    backup_sources: list[str] = Field(default_factory=list)
    excluded: list[ExcludedDatabase] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    classifications: list[DatabaseClassification] = Field(default_factory=list)


def build_backup_plan(database_names: list[str]) -> BackupPlanDryRun:
    """Build a dry-run backup plan from database names."""
    plan = BackupPlanDryRun(safety_notes=list(SAFETY_NOTES))

    for name in database_names:
        classification = classify_database(name)
        plan.classifications.append(classification)
        reason = exclusion_reason(classification)
        if reason is None:
            plan.backup_sources.append(name)
        else:
            plan.excluded.append(
                ExcludedDatabase(
                    name=name,
                    role=classification.role.value,
                    reason=reason,
                )
            )

    return plan


def build_backup_plan_from_inventory(inventory: DatabaseInventory) -> BackupPlanDryRun:
    return build_backup_plan(inventory.names)


def build_demo_backup_plan() -> BackupPlanDryRun:
    return build_backup_plan(DEMO_DATABASES)


def build_discovered_backup_plan() -> BackupPlanDryRun:
    inventory = discover_from_config(include_catalog=True)
    return build_backup_plan_from_inventory(inventory)
