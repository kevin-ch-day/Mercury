"""
Database module core.

Models, platform catalog, naming classifier, config loaders,
inventory records, and provenance labels. No discovery, MariaDB,
planning, policy, or CLI — those live in sibling packages.
"""

from mercury.database.core.catalog import (
    CATALOG_BY_NAME,
    PLATFORM_CATALOG,
    PLATFORM_DATABASES,
    CatalogEntry,
)
from mercury.database.core.classifier import (
    DEV_SUFFIX,
    PROD_SUFFIX,
    RESTORE_CHECK_PREFIX,
    SHARED_AUTHORITY_NAME,
    DatabaseClassification,
    DatabaseRole,
    classify_database,
    exclusion_reason,
)
from mercury.database.core.config_files import (
    configured_database_names,
    load_databases_from_file,
    parse_host_port,
)
from mercury.database.core.inventory import inventory_summary, record_from_name
from mercury.database.core.inventory_ops import (
    backup_source_entries,
    backup_source_names,
    classify_inventory,
    dev_target_entries,
    entries_by_role,
    entry_by_name,
    format_entry_line,
    format_entry_columns,
    inventory_role_summary,
    is_live_inventory,
    projects_map,
    role_env_label,
    shared_authority_note,
    source_role_label,
    sort_entries_for_display,
    sync_role_label,
)
from mercury.database.core.models import DatabaseInventory, DatabaseRecord
from mercury.database.core.scope import (
    ACTIVE_BACKUP_SOURCE_DATABASES,
    ACTIVE_DEV_TARGET_DATABASES,
    is_active_backup_source,
    is_active_dev_target,
    is_active_sync_pair,
    OUT_OF_SCOPE_DATABASES,
    filter_inventory,
    filter_in_scope_names,
    is_in_scope,
)
from mercury.database.core.sources import (
    SOURCE_CATALOG,
    SOURCE_EXAMPLE,
    SOURCE_LIVE,
    SOURCE_LOCAL,
)

__all__ = [
    "DatabaseRecord",
    "DatabaseInventory",
    "CatalogEntry",
    "PLATFORM_CATALOG",
    "PLATFORM_DATABASES",
    "CATALOG_BY_NAME",
    "DatabaseRole",
    "DatabaseClassification",
    "classify_database",
    "exclusion_reason",
    "SHARED_AUTHORITY_NAME",
    "RESTORE_CHECK_PREFIX",
    "PROD_SUFFIX",
    "DEV_SUFFIX",
    "load_databases_from_file",
    "configured_database_names",
    "parse_host_port",
    "record_from_name",
    "inventory_summary",
    "backup_source_names",
    "backup_source_entries",
    "dev_target_entries",
    "entries_by_role",
    "entry_by_name",
    "projects_map",
    "classify_inventory",
    "is_live_inventory",
    "format_entry_line",
    "format_entry_columns",
    "inventory_role_summary",
    "role_env_label",
    "source_role_label",
    "sync_role_label",
    "shared_authority_note",
    "sort_entries_for_display",
    "ACTIVE_BACKUP_SOURCE_DATABASES",
    "ACTIVE_DEV_TARGET_DATABASES",
    "is_active_backup_source",
    "is_active_dev_target",
    "is_active_sync_pair",
    "OUT_OF_SCOPE_DATABASES",
    "filter_inventory",
    "filter_in_scope_names",
    "is_in_scope",
    "SOURCE_LOCAL",
    "SOURCE_EXAMPLE",
    "SOURCE_CATALOG",
    "SOURCE_LIVE",
]
