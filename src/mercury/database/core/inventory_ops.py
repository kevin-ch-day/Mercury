"""Operations on a DatabaseInventory."""

from mercury.database.core.models import DatabaseInventory, DatabaseRecord


def backup_source_entries(inventory: DatabaseInventory) -> list[DatabaseRecord]:
    return [e for e in inventory.entries if e.backup_source]


def backup_source_names(inventory: DatabaseInventory) -> list[str]:
    return [e.name for e in backup_source_entries(inventory)]


def dev_target_entries(inventory: DatabaseInventory) -> list[DatabaseRecord]:
    return [e for e in inventory.entries if e.dev_target]


def entries_by_role(inventory: DatabaseInventory) -> dict[str, list[DatabaseRecord]]:
    grouped: dict[str, list[DatabaseRecord]] = {}
    for entry in inventory.entries:
        grouped.setdefault(entry.role, []).append(entry)
    return grouped


def entry_by_name(inventory: DatabaseInventory, name: str) -> DatabaseRecord | None:
    for entry in inventory.entries:
        if entry.name == name:
            return entry
    return None


def projects_map(inventory: DatabaseInventory) -> dict[str, str]:
    return {e.name: e.project for e in inventory.entries if e.project}


def classify_inventory(inventory: DatabaseInventory) -> dict[str, list[str]]:
    """Group database names by classified role string."""
    groups: dict[str, list[str]] = {}
    for entry in inventory.entries:
        groups.setdefault(entry.role, []).append(entry.name)
    return {role: sorted(names) for role, names in groups.items()}


def is_live_inventory(inventory: DatabaseInventory) -> bool:
    return inventory.mode == "mariadb_readonly" or inventory.connection.startswith("connected")


def format_entry_line(entry: DatabaseRecord) -> str:
    """Single-line summary matching print_inventory style."""
    flags = []
    if entry.backup_source:
        flags.append("backup_source")
    if entry.dev_target:
        flags.append("dev_target")
    if entry.manual_review:
        flags.append("manual_review")
    flag_text = f" ({', '.join(flags)})" if flags else ""
    endpoint = ""
    if entry.host:
        port = entry.port if entry.port is not None else "?"
        endpoint = f" @ {entry.host}:{port}"
    project = f" [{entry.project}]" if entry.project else ""
    return f"{entry.name}{project} <{entry.role}>{flag_text}{endpoint} — {entry.config_source}"
