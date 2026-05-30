"""Live read-only MariaDB discovery (SHOW DATABASES only)."""

from mercury.database.core import DatabaseInventory, SOURCE_LIVE, record_from_name
from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.paths import LOCAL_CONFIG

SYSTEM_DATABASES = frozenset(
    {
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
    }
)

READ_ONLY_SQL = "SHOW DATABASES"


class MariaDbLiveError(Exception):
    """Live discovery failed."""


class MariaDbDriverMissingError(MariaDbLiveError):
    """pymysql is not installed."""


def _import_pymysql():
    try:
        import pymysql
    except ImportError as exc:
        raise MariaDbDriverMissingError(
            "pymysql is required for live discovery. Install with: "
            'pip install -e ".[mariadb]"'
        ) from exc
    return pymysql


def fetch_database_names(
    config: MariaDbConnectionConfig,
    *,
    connect_fn=None,
) -> list[str]:
    """Run SHOW DATABASES on the server (read-only)."""
    if connect_fn is not None:
        return _filter_user_databases(connect_fn(config))

    pymysql = _import_pymysql()
    connect_kwargs: dict = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "connect_timeout": config.connect_timeout,
        "read_timeout": 30,
        "charset": "utf8mb4",
    }
    if config.ssl_disabled:
        connect_kwargs["ssl"] = None

    try:
        connection = pymysql.connect(**connect_kwargs)
    except pymysql.Error as exc:
        raise MariaDbLiveError(
            f"Could not connect to MariaDB at {config.host}:{config.port}: {exc}"
        ) from exc

    try:
        with connection.cursor() as cursor:
            cursor.execute(READ_ONLY_SQL)
            rows = cursor.fetchall()
    except pymysql.Error as exc:
        raise MariaDbLiveError(f"SHOW DATABASES failed: {exc}") from exc
    finally:
        connection.close()

    names: list[str] = []
    for row in rows:
        if not row:
            continue
        name = row[0] if isinstance(row, (tuple, list)) else row
        names.append(str(name))
    return _filter_user_databases(names)


def _filter_user_databases(names: list[str]) -> list[str]:
    return sorted(n for n in names if n.lower() not in SYSTEM_DATABASES)


def discover_databases_live(
    config: MariaDbConnectionConfig,
    *,
    connect_fn=None,
) -> DatabaseInventory:
    """Discover databases via live read-only SHOW DATABASES."""
    names = fetch_database_names(config, connect_fn=connect_fn)
    inventory = DatabaseInventory(
        connection=f"connected ({config.host}:{config.port})",
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
