"""Databases excluded from Mercury discovery, sync, and reporting."""

from __future__ import annotations

from mercury.database.core.models import DatabaseInventory, DatabaseRecord

# Not managed by Mercury — hidden from inventory and prod→dev sync planning.
OUT_OF_SCOPE_DATABASES: frozenset[str] = frozenset(
    {
        "proofpoint_cti_db_dev",
        "droid_threat_intel_db_dev",
        "droid_threat_intel_db_prod",
    }
)


def is_in_scope(name: str) -> bool:
    return name not in OUT_OF_SCOPE_DATABASES


def filter_in_scope_names(names: list[str]) -> list[str]:
    return [name for name in names if is_in_scope(name)]


def filter_inventory(inventory: DatabaseInventory) -> DatabaseInventory:
    """Drop out-of-scope databases from an inventory snapshot."""
    entries = [entry for entry in inventory.entries if is_in_scope(entry.name)]
    return inventory.model_copy(update={"entries": entries})
