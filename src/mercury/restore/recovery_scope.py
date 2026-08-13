"""Current production recovery, development rebuild, and snapshot scopes."""

from __future__ import annotations

from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES, ACTIVE_DEV_RECOVERY_DATABASES
from mercury.database.prod_dev_pairs import build_prod_dev_pairs

# Only authoritative production/shared sources define routine DR readiness.
PRODUCTION_RECOVERY_DATABASES: tuple[str, ...] = tuple(sorted(ACTIVE_BACKUP_SOURCE_DATABASES))

# Explicit optional captures.  Their absence is a normal state.
DEVELOPMENT_SNAPSHOT_DATABASES: tuple[str, ...] = tuple(sorted(ACTIVE_DEV_RECOVERY_DATABASES))


def development_rebuild_pairs():
    """Approved pairs only; never infer a source from a name suffix."""
    names = list(PRODUCTION_RECOVERY_DATABASES) + list(DEVELOPMENT_SNAPSHOT_DATABASES)
    return tuple(build_prod_dev_pairs(names))


# Compatibility exports for callers that previously used the historical seven-
# schema workstation acceptance list. They are no longer DR authority.
REQUIRED_RECOVERY_DATABASES = PRODUCTION_RECOVERY_DATABASES
REQUIRED_RECOVERY_PRODUCTION = PRODUCTION_RECOVERY_DATABASES
REQUIRED_RECOVERY_DEVELOPMENT: tuple[str, ...] = ()


def is_required_recovery_database(name: str) -> bool:
    return name in PRODUCTION_RECOVERY_DATABASES


def is_required_recovery_production(name: str) -> bool:
    return name in PRODUCTION_RECOVERY_DATABASES
