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


def _load_manifest_object_inventory(manifest_path: Path):
    """Return the exact full-dump object contract when the manifest has one.

    A logical restore can successfully create tables while silently losing a
    trigger, routine, view, or event.  When the source backup recorded its
    named object contract, restore verification must compare every object class
    before a disposable restore-check is marked successful.
    """
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        contract = data.get("object_contract")
        if not isinstance(contract, dict):
            return None
        raw = contract.get("dump")
        if not isinstance(raw, dict):
            return None
        from mercury.backup.content_contract import BackupObjectInventory

        return BackupObjectInventory.model_validate(raw)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def verify_deployed_database(
    database: str,
    *,
    manifest_path: Path,
    config: MariaDbConnectionConfig | None = None,
    row_fn=None,
    inventory_fn=None,
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

    expected_inventory = _load_manifest_object_inventory(manifest_path)
    if expected_inventory is not None:
        try:
            from mercury.backup.content_contract import (
                compare_object_inventories,
                fetch_live_object_inventory,
            )

            fetch_inventory = inventory_fn or fetch_live_object_inventory
            observed_inventory = fetch_inventory(cfg, database)
            mismatches = compare_object_inventories(
                expected_inventory, observed_inventory
            )
            if mismatches:
                issues.extend(
                    f"object contract: {mismatch}" for mismatch in mismatches
                )
            else:
                detail = "manifest object contract verified (tables, views, triggers, routines, events)"
        except Exception as exc:  # noqa: BLE001 - a restore-check must fail closed.
            issues.append(f"could not verify manifest object contract: {exc}")

    verified = inspect.exists_on_server and not issues
    return DeploymentVerification(
        database=database,
        exists_on_server=inspect.exists_on_server,
        table_count=table_count,
        verified=verified,
        detail=detail,
        issues=issues,
    )
