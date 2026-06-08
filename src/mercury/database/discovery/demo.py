"""Demo discovery: config + platform catalog, no MariaDB connection."""

from mercury.database.discovery.config import discover_from_config
from mercury.database.core import DatabaseInventory
from mercury.database.core.scope import filter_inventory


def discover_demo() -> DatabaseInventory:
    """Platform demo discovery for Windows seed / offline planning."""
    inventory = filter_inventory(discover_from_config(include_catalog=True))
    inventory.mode = "demo"
    inventory.connection = "not_connected"
    return inventory
