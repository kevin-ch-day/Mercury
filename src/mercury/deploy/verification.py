"""Post-deployment verification for imported databases."""

from __future__ import annotations

import json
from pathlib import Path

from mercury.database.mariadb.config import MariaDbConnectionConfig
from mercury.database.mariadb.identifiers import quote_ident
from mercury.database.mariadb.inspect import inspect_database_on_server
from mercury.database.mariadb.session import readonly_scalar, try_load_mariadb_config
from mercury.deploy.models import DeploymentVerification


def _load_manifest_row_counts(manifest_path: Path) -> dict[str, int] | None:
    if not manifest_path.is_file():
        raise ValueError("Verification manifest is missing")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError("Verification manifest cannot be read") from exc
    if not isinstance(data, dict):
        raise ValueError("Verification manifest must be an object")
    for key in ("row_counts", "table_row_counts", "row_count_inventory"):
        if key not in data:
            continue
        raw = data[key]
        if not isinstance(raw, dict) or not raw:
            raise ValueError(f"Manifest {key} must be a nonempty table/count map")
        parsed: dict[str, int] = {}
        for table, count in raw.items():
            if not isinstance(table, str) or type(count) is not int or count < 0:
                raise ValueError(f"Manifest {key} contains an invalid table/count entry")
            quote_ident(table)
            parsed[table] = count
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
        raise ValueError("Verification manifest is missing")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Verification manifest must be an object")
        contract = data.get("object_contract")
        if contract is None:
            return None
        from mercury.backup.content_contract import BackupContentContract

        parsed = BackupContentContract.model_validate(contract)
        if not parsed.verified or parsed.issues:
            raise ValueError("Recorded object contract is not verified")
        return parsed.dump
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Verification manifest cannot be read: {exc}") from exc


def verify_deployed_database(
    database: str,
    *,
    manifest_path: Path,
    config: MariaDbConnectionConfig | None = None,
    row_fn=None,
    inventory_fn=None,
    count_fn=None,
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

    try:
        row_counts = _load_manifest_row_counts(manifest_path)
    except ValueError as exc:
        issues.append(str(exc))
        row_counts = None
    if row_counts:
        detail = f"manifest row-count inventory compared for {len(row_counts)} tables"
        if table_count is not None and table_count < len(row_counts):
            issues.append(
                f"table count {table_count} is lower than manifest inventory ({len(row_counts)} tables)"
            )
        elif table_count is None:
            issues.append("could not read table count for row-count comparison")
        fetch_count = count_fn or readonly_scalar
        for table, expected in sorted(row_counts.items()):
            try:
                observed = int(fetch_count(cfg, f"SELECT COUNT(*) FROM {quote_ident(database)}.{quote_ident(table)}"))
            except Exception as exc:  # noqa: BLE001 - missing counts cannot pass verification.
                issues.append(f"could not count rows in {table}: {exc}")
                continue
            if observed != expected:
                issues.append(f"row count differs for {table}: expected {expected}, observed {observed}")

    try:
        expected_inventory = _load_manifest_object_inventory(manifest_path)
    except ValueError as exc:
        issues.append(f"could not verify manifest object contract: {exc}")
        expected_inventory = None
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
                detail = "manifest object contract verified (tables, views, triggers, routines, events)" + (
                    f"; row-count inventory compared for {len(row_counts)} tables" if row_counts else ""
                )
        except Exception as exc:  # noqa: BLE001 - a restore-check must fail closed.
            issues.append(f"could not verify manifest object contract: {exc}")
    else:
        issues.append("manifest lacks a verified object contract; target cannot be qualified")

    verified = inspect.exists_on_server and not issues
    return DeploymentVerification(
        database=database,
        exists_on_server=inspect.exists_on_server,
        table_count=table_count,
        verified=verified,
        detail=detail,
        issues=issues,
    )
