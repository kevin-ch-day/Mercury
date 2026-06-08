"""Build inventory records and summaries."""

from mercury.database.core.catalog import CATALOG_BY_NAME
from mercury.database.core.classifier import DatabaseClassification, classify_database
from mercury.database.core.models import DatabaseInventory, DatabaseRecord
from mercury.database.core.scope import is_in_scope


def record_from_name(
    name: str,
    config_source: str,
    *,
    host: str | None = None,
    port: int | None = None,
    connected: bool = False,
) -> DatabaseRecord:
    c: DatabaseClassification = classify_database(name)
    catalog = CATALOG_BY_NAME.get(name)
    backup_source = c.backup_source
    dev_target = c.dev_target
    manual_review = c.manual_review
    if not is_in_scope(name):
        backup_source = False
        dev_target = False
        manual_review = False
    return DatabaseRecord(
        name=name,
        role=c.role.value,
        backup_source=backup_source,
        dev_target=dev_target,
        manual_review=manual_review,
        project=catalog.project if catalog else None,
        host=host,
        port=port,
        config_source=config_source,
        connected=connected,
    )


def inventory_summary(inventory: DatabaseInventory) -> dict[str, int]:
    """Count entries by role for display."""
    counts: dict[str, int] = {}
    for entry in inventory.entries:
        counts[entry.role] = counts.get(entry.role, 0) + 1
    return counts
