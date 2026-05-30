"""Load database name lists from TOML config files."""

from pathlib import Path

import tomllib

import mercury.paths as paths


def load_databases_from_file(path: Path) -> dict[str, dict[str, object]]:
    """Load [databases] table from a TOML file. Returns empty dict if missing."""
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    raw = data.get("databases", {})
    if not isinstance(raw, dict):
        return {}
    return {str(name): entry for name, entry in raw.items() if isinstance(entry, dict)}


def parse_host_port(entry: object) -> tuple[str | None, int | None]:
    if not isinstance(entry, dict):
        return None, None
    host = entry.get("host")
    port = entry.get("port")
    return (
        str(host) if host is not None else None,
        int(port) if port is not None else None,
    )


def configured_database_names() -> list[str]:
    """Names from local config, else example config, else empty."""
    if paths.DATABASES_LOCAL.exists():
        return sorted(load_databases_from_file(paths.DATABASES_LOCAL))
    if paths.DATABASES_EXAMPLE.exists():
        return sorted(load_databases_from_file(paths.DATABASES_EXAMPLE))
    return []
