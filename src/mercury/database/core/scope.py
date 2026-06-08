"""Active Mercury milestone scope controls."""

from __future__ import annotations

from mercury.database.core.models import DatabaseInventory

ACTIVE_BACKUP_SOURCE_DATABASES: frozenset[str] = frozenset(
    {
        "android_permission_intel",
        "erebus_threat_intel_prod",
        "scytaledroid_core_prod",
    }
)

ACTIVE_DEV_TARGET_DATABASES: frozenset[str] = frozenset(
    {
        "erebus_threat_intel_dev",
        "scytaledroid_core_dev",
    }
)

OUT_OF_SCOPE_DATABASES: frozenset[str] = frozenset(
    {
        "android_permission_intel_prod",
        "android_permission_intel_dev",
        "gecko_research_database_prod",
        "gecko_research_database_dev",
        "proofpoint_cti_db_dev",
        "droid_threat_intel_db_dev",
        "droid_threat_intel_db_prod",
    }
)


def is_in_scope(name: str) -> bool:
    return name not in OUT_OF_SCOPE_DATABASES


def is_active_backup_source(name: str) -> bool:
    return name in ACTIVE_BACKUP_SOURCE_DATABASES


def is_active_dev_target(name: str) -> bool:
    return name in ACTIVE_DEV_TARGET_DATABASES


def is_active_sync_pair(prod_name: str, dev_name: str) -> bool:
    return is_active_backup_source(prod_name) and is_active_dev_target(dev_name)


def filter_in_scope_names(names: list[str]) -> list[str]:
    return [name for name in names if is_in_scope(name)]


def filter_inventory(inventory: DatabaseInventory) -> DatabaseInventory:
    """Drop out-of-scope databases from an inventory snapshot."""
    entries = [entry for entry in inventory.entries if is_in_scope(entry.name)]
    return inventory.model_copy(update={"entries": entries})
