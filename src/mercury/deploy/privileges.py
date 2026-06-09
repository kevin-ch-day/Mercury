"""Read-only MariaDB grant checks for deployment readiness."""

from __future__ import annotations

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.errors import MariaDbLiveError
from mercury.database.mariadb.session import readonly_scalars

DEPLOYMENT_GRANT_KEYWORDS = (
    "ALL PRIVILEGES",
    "CREATE",
    "INSERT",
)


def fetch_current_grants_text(config: MariaDbConnectionConfig) -> str:
    rows = readonly_scalars(config, "SHOW GRANTS FOR CURRENT_USER()")
    return " ".join(rows)


def deployment_grants_sufficient(config: MariaDbConnectionConfig) -> tuple[bool, str]:
    """Return whether configured user can create databases and import dumps."""
    try:
        grants = fetch_current_grants_text(config).upper()
    except MariaDbLiveError as exc:
        return False, str(exc)

    if "ALL PRIVILEGES" in grants or "GRANT ALL" in grants:
        return True, "all privileges"

    missing: list[str] = []
    for keyword in ("CREATE", "INSERT"):
        if keyword not in grants:
            missing.append(keyword)
    if missing:
        return False, f"missing grants: {', '.join(missing)}"
    return True, "create/insert grants present"


def deployment_grant_repair_sql(user: str) -> list[str]:
    """SQL steps for an operator running sudo mariadb (never executed by Mercury)."""
    return [
        "sudo mariadb",
        (
            "GRANT CREATE, INSERT, UPDATE, DELETE, ALTER, INDEX, "
            "CREATE TEMPORARY TABLES, CREATE VIEW, CREATE ROUTINE, "
            "ALTER ROUTINE, EXECUTE, TRIGGER, EVENT "
            f"ON *.* TO '{user}'@'localhost';"
        ),
        "FLUSH PRIVILEGES;",
        f"SHOW GRANTS FOR '{user}'@'localhost';",
    ]
