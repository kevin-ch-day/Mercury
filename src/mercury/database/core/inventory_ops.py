"""Operations on a DatabaseInventory."""

from mercury.database.core.classifier import DatabaseRole, classify_database
from mercury.database.core.models import DatabaseInventory, DatabaseRecord
from mercury.database.core.scope import (
    ACTIVE_BACKUP_SOURCE_DATABASES,
    ACTIVE_DEV_TARGET_DATABASES,
    is_active_backup_source,
    is_active_dev_target,
    is_active_sync_source,
)


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


def format_entry_line(entry: DatabaseRecord, *, compact: bool = False) -> str:
    """Single-line summary matching print_inventory style."""
    if compact:
        return format_entry_columns(entry)

    project = f" [{entry.project}]" if entry.project else ""
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


ROLE_ENV_LABELS: dict[str, str] = {
    "production": "PROD",
    "development": "DEV",
    "shared_authority": "SHARED",
    "restore_check_temp": "TEMP",
    "unknown": "OTHER",
}

ROLE_SORT_ORDER: dict[str, int] = {
    "production": 0,
    "shared_authority": 1,
    "development": 2,
    "unknown": 3,
    "restore_check_temp": 4,
}


def role_env_label(role: str) -> str:
    """Short PROD/DEV/SHARED label parsed from platform role."""
    return ROLE_ENV_LABELS.get(role, role.upper())


def source_role_label(name: str) -> str:
    """Operator-facing source role label for an in-scope database name."""
    role = classify_database(name).role
    if role == DatabaseRole.SHARED_AUTHORITY:
        return "shared authority source"
    if role == DatabaseRole.PRODUCTION:
        return "production source"
    if role == DatabaseRole.DEVELOPMENT:
        return "development target"
    if role == DatabaseRole.RESTORE_CHECK_TEMP:
        return "restore-check temp"
    return "other"


def sync_role_label(name: str) -> str:
    """Operator-facing sync role label for active-scope discovery screens."""
    if is_active_sync_source(name):
        return "source+pair"
    if is_active_backup_source(name):
        return "backup-only"
    if is_active_dev_target(name):
        return "dev target"
    return "n/a"


def shared_authority_note() -> str:
    return (
        "Shared authority databases are backup-only and do not appear in prod-to-dev sync pairs."
    )


def format_entry_columns(entry: DatabaseRecord) -> str:
    """Compact columnar line: name, ENV, backup, optional project."""
    backup = "yes" if entry.backup_source else "no"
    project = entry.project or ""
    if project:
        return f"{entry.name}  {role_env_label(entry.role):<6}  {backup:<3}  {project}"
    return f"{entry.name}  {role_env_label(entry.role):<6}  {backup}"


def inventory_role_summary(counts: dict[str, int]) -> str:
    """Human summary like '3 prod, 3 dev, 1 shared'."""
    labels = {
        "production": "prod",
        "development": "dev",
        "shared_authority": "shared",
        "unknown": "other",
        "restore_check_temp": "temp",
    }
    ordered_roles = sorted(counts.keys(), key=lambda role: ROLE_SORT_ORDER.get(role, 99))
    parts = [f"{counts[role]} {labels.get(role, role)}" for role in ordered_roles]
    return ", ".join(parts)


def sort_entries_for_display(entries: list[DatabaseRecord]) -> list[DatabaseRecord]:
    return sorted(
        entries,
        key=lambda entry: (ROLE_SORT_ORDER.get(entry.role, 99), entry.name),
    )
