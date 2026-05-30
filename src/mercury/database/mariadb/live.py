"""Live read-only MariaDB discovery (SHOW DATABASES only)."""

from mercury.database.core import DatabaseInventory, SOURCE_LIVE, record_from_name
from mercury.database.mariadb.client import connection_label
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.session import fetch_user_database_names
from mercury.core.paths import LOCAL_CONFIG

READ_ONLY_SQL = "SHOW DATABASES"


def discover_databases_live(
    config: MariaDbConnectionConfig,
    *,
    connect_fn=None,
) -> DatabaseInventory:
    """Discover databases via live read-only SHOW DATABASES."""
    names = fetch_user_database_names(config, connect_fn=connect_fn)
    inventory = DatabaseInventory(
        connection=connection_label(config),
        mode="mariadb_readonly",
        primary_config=str(LOCAL_CONFIG.name),
    )
    inventory.entries = [
        record_from_name(
            name,
            SOURCE_LIVE,
            host=config.host,
            port=config.port,
            connected=True,
        )
        for name in names
    ]
    return inventory
