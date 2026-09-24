"""Active Mercury milestone scope controls."""

from __future__ import annotations

from mercury.database.core.models import DatabaseInventory

# ObsidianDroid production source-of-truth (backup-only; no automatic prod→dev sync).
OBSIDIANDROID_PROD_DATABASE = "obsidiandroid_core_prod"

ACTIVE_BACKUP_SOURCE_DATABASES: frozenset[str] = frozenset(
    {
        "android_permission_intel",
        "erebus_threat_intel_prod",
        "erebus_provider_secrets_prod",
        "scytaledroid_core_prod",
        OBSIDIANDROID_PROD_DATABASE,
    }
)

ACTIVE_DEV_TARGET_DATABASES: frozenset[str] = frozenset(
    {
        "android_permission_intel_dev",
        "erebus_threat_intel_dev",
        "scytaledroid_core_dev",
    }
)

# Optional recovery copies for a workstation move.  Matches the approved
# disposable prod→dev clones (Permission Intel, Erebus, ScytaleDroid).
ACTIVE_DEV_RECOVERY_DATABASES: frozenset[str] = frozenset(ACTIVE_DEV_TARGET_DATABASES)

OUT_OF_SCOPE_DATABASES: frozenset[str] = frozenset(
    {
        "android_permission_intel_prod",
        # Legacy Komodo / market-event research naming (not ObsidianDroid prod).
        "gecko_research_database_prod",
        "gecko_research_database_dev",
        # ObsidianDroid dev is not a Mercury sync target unless explicitly configured.
        "obsidiandroid_core_dev",
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


def is_active_dev_recovery_database(name: str) -> bool:
    """True only for explicitly approved optional dev recovery backups."""
    return name in ACTIVE_DEV_RECOVERY_DATABASES


def is_active_sync_pair(prod_name: str, dev_name: str) -> bool:
    from mercury.database.prod_dev_pairs import is_approved_sync_pair

    return is_approved_sync_pair(prod_name, dev_name)


def is_active_sync_source(prod_name: str) -> bool:
    """True when an active backup source also has a configured prod→dev sync pair."""
    from mercury.database.prod_dev_pairs import prod_to_dev_name

    expected_dev = prod_to_dev_name(prod_name)
    if expected_dev is None:
        return False
    return is_active_sync_pair(prod_name, expected_dev)


def filter_inventory(inventory: DatabaseInventory) -> DatabaseInventory:
    """Drop out-of-scope databases from an inventory snapshot."""
    entries = [entry for entry in inventory.entries if is_in_scope(entry.name)]
    return inventory.model_copy(update={"entries": entries})
