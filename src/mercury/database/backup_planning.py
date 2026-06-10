"""Dry-run backup planning from database names or inventory."""

from pydantic import BaseModel, Field

from mercury.database.core import (
    CATALOG_BY_NAME,
    PLATFORM_DATABASES,
    DatabaseClassification,
    DatabaseInventory,
    classify_database,
    exclusion_reason,
    is_active_backup_source,
    is_in_scope,
)
from mercury.database.discovery import discover_from_config
from mercury.core.safety import SAFETY_NOTES

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

    for name in sorted(set(database_names)):
        classification = classify_database(name)
        plan.classifications.append(classification)
        if not is_in_scope(name):
            reason = "Out of active Mercury scope for this milestone."
        elif (
            classification.backup_source
            and name in CATALOG_BY_NAME
            and not is_active_backup_source(name)
        ):
            reason = "Not an active Mercury backup source for this milestone."
        else:
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


def anchor_planning_names(inventory: DatabaseInventory, *, live: bool) -> list[str]:
    """Union live inventory with configured protected backup sources when planning live."""
    if not live:
        return list(inventory.names)
    from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES

    return sorted(set(inventory.names) | ACTIVE_BACKUP_SOURCE_DATABASES)


def build_backup_plan_from_inventory(
    inventory: DatabaseInventory,
    *,
    live: bool = False,
) -> BackupPlanDryRun:
    return build_backup_plan(anchor_planning_names(inventory, live=live))


def build_demo_backup_plan() -> BackupPlanDryRun:
    return build_backup_plan(DEMO_DATABASES)


def build_discovered_backup_plan() -> BackupPlanDryRun:
    inventory = discover_from_config(include_catalog=True)
    return build_backup_plan_from_inventory(inventory)
