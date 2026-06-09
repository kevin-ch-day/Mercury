"""Post-deployment verification for imported databases."""

from __future__ import annotations

import json
from pathlib import Path

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.session import try_load_mariadb_config
from mercury.deploy.models import DeploymentVerification


def _load_manifest_row_counts(manifest_path: Path) -> dict[str, int] | None:
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    for key in ("row_counts", "table_row_counts", "row_count_inventory"):
        raw = data.get(key)
        if isinstance(raw, dict) and raw:
            parsed: dict[str, int] = {}
            for table, count in raw.items():
                try:
                    parsed[str(table)] = int(count)
                except (TypeError, ValueError):
                    continue
            if parsed:
                return parsed
    return None


def verify_deployed_database(
    database: str,
    *,
    manifest_path: Path,
    config: MariaDbConnectionConfig | None = None,
    row_fn=None,
) -> DeploymentVerification:
    cfg = config or try_load_mariadb_config()
    issues: list[str] = []
    detail = "basic verification only; manifest lacks row-count inventory"

    if cfg is None:
        return DeploymentVerification(
            database=database,
            detail="MariaDB config unavailable for verification",
            issues=["config missing"],
        )

    inspect = inspect_database_on_server(database, cfg, row_fn=row_fn)
    if inspect.error:
        issues.append(inspect.error)
    if not inspect.exists_on_server:
        issues.append("database not found after import")
    table_count = inspect.table_count
    if table_count is not None and table_count <= 0:
        issues.append("table count is zero")
    if inspect.total_bytes is not None and inspect.total_bytes <= 0:
        issues.append("database size is zero")

    row_counts = _load_manifest_row_counts(manifest_path)
    if row_counts:
        detail = f"manifest row-count inventory covers {len(row_counts)} tables"
        if table_count is not None and table_count < len(row_counts):
            issues.append(
                f"table count {table_count} is lower than manifest inventory ({len(row_counts)} tables)"
            )
        elif table_count is None:
            issues.append("could not read table count for row-count comparison")

    verified = inspect.exists_on_server and not issues
    return DeploymentVerification(
        database=database,
        exists_on_server=inspect.exists_on_server,
        table_count=table_count,
        verified=verified,
        detail=detail,
        issues=issues,
    )
