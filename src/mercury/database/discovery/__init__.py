"""Database discovery: config, demo catalog, or live MariaDB."""

from typing import Literal

from mercury.database.discovery.config import discover_from_config
from mercury.database.discovery.demo import discover_demo
from mercury.database.core import DatabaseInventory

DiscoveryMode = Literal["demo", "config", "live"]


def discover(
    mode: DiscoveryMode = "config",
    *,
    include_catalog: bool = True,
    prefer_local: bool = True,
    mariadb_config=None,
    connect_fn=None,
) -> DatabaseInventory:
    """
    Unified discovery entry point.

    - demo: config + platform catalog (no server)
    - config: same as demo but mode label config_and_catalog
    - live: SHOW DATABASES via mercury.database.mariadb (requires config + pymysql)
    """
    if mode == "live":
        from mercury.database.mariadb import discover_databases_live, load_mariadb_config

        cfg = mariadb_config or load_mariadb_config()
        inventory = discover_databases_live(cfg, connect_fn=connect_fn)
    elif mode == "demo":
        inventory = discover_demo()
    else:
        inventory = discover_from_config(
            include_catalog=include_catalog,
            prefer_local=prefer_local,
        )

    from mercury.database.core.scope import filter_inventory
    from mercury.logging.events import log_inventory_discovered

    if mode != "live":
        inventory = filter_inventory(inventory)

    log_inventory_discovered(mode=inventory.mode, count=inventory.count, connection=inventory.connection)
    return inventory


__all__ = [
    "discover",
    "discover_from_config",
    "discover_demo",
    "DiscoveryMode",
]
