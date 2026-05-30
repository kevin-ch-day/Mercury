"""Discover databases from TOML config and platform catalog (no server)."""

from pathlib import Path

from mercury.database.core import (
    PLATFORM_DATABASES,
    SOURCE_CATALOG,
    SOURCE_EXAMPLE,
    SOURCE_LOCAL,
    DatabaseInventory,
    DatabaseRecord,
    load_databases_from_file,
    parse_host_port,
    record_from_name,
)
import mercury.paths as paths


def discover_from_config(
    *,
    include_catalog: bool = True,
    prefer_local: bool = True,
) -> DatabaseInventory:
    """
    Build inventory from config files and optional platform catalog.

    No TCP/socket connections. Host/port values come only from TOML.
    """
    inventory = DatabaseInventory()
    merged: dict[str, DatabaseRecord] = {}

    config_chain: list[tuple[Path, str]] = []
    if prefer_local and paths.DATABASES_LOCAL.exists():
        config_chain.append((paths.DATABASES_LOCAL, SOURCE_LOCAL))
        inventory.primary_config = SOURCE_LOCAL
    elif paths.DATABASES_EXAMPLE.exists():
        config_chain.append((paths.DATABASES_EXAMPLE, SOURCE_EXAMPLE))
        inventory.primary_config = SOURCE_EXAMPLE

    if paths.DATABASES_LOCAL.exists() and paths.DATABASES_EXAMPLE.exists():
        if inventory.primary_config == SOURCE_LOCAL:
            config_chain.append((paths.DATABASES_EXAMPLE, SOURCE_EXAMPLE))

    for path, source_label in config_chain:
        for name, entry in load_databases_from_file(path).items():
            host, port = parse_host_port(entry)
            merged[name] = record_from_name(name, source_label, host=host, port=port)

    if include_catalog:
        for name in PLATFORM_DATABASES:
            if name not in merged:
                merged[name] = record_from_name(name, SOURCE_CATALOG)

    inventory.entries = sorted(merged.values(), key=lambda r: r.name)
    return inventory
