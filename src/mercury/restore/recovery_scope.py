"""Current production recovery, development rebuild, and snapshot scopes."""

from __future__ import annotations

from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES, ACTIVE_DEV_RECOVERY_DATABASES

# Only authoritative production/shared sources define routine DR readiness.
PRODUCTION_RECOVERY_DATABASES: tuple[str, ...] = tuple(sorted(ACTIVE_BACKUP_SOURCE_DATABASES))

# Explicit optional captures.  Their absence is a normal state.
DEVELOPMENT_SNAPSHOT_DATABASES: tuple[str, ...] = tuple(sorted(ACTIVE_DEV_RECOVERY_DATABASES))


# Compatibility exports for callers that previously used the historical seven-
# schema workstation acceptance list. They are no longer DR authority.
REQUIRED_RECOVERY_DATABASES = PRODUCTION_RECOVERY_DATABASES
REQUIRED_RECOVERY_PRODUCTION = PRODUCTION_RECOVERY_DATABASES
REQUIRED_RECOVERY_DEVELOPMENT: tuple[str, ...] = ()


def is_required_recovery_production(name: str) -> bool:
    return name in PRODUCTION_RECOVERY_DATABASES
