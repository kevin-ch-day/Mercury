"""Required recovery database scope (seven-schema platform)."""

from __future__ import annotations

from mercury.database.core.scope import (
    ACTIVE_BACKUP_SOURCE_DATABASES,
    ACTIVE_DEV_RECOVERY_DATABASES,
)

# Operator-facing order for the Restore and Disaster Recovery dashboard.
REQUIRED_RECOVERY_DATABASES: tuple[str, ...] = (
    "android_permission_intel",
    "erebus_threat_intel_prod",
    "obsidiandroid_core_prod",
    "scytaledroid_core_prod",
    "android_permission_intel_dev",
    "erebus_threat_intel_dev",
    "scytaledroid_core_dev",
)

REQUIRED_RECOVERY_PRODUCTION: tuple[str, ...] = tuple(
    name
    for name in REQUIRED_RECOVERY_DATABASES
    if name in ACTIVE_BACKUP_SOURCE_DATABASES
)
REQUIRED_RECOVERY_DEVELOPMENT: tuple[str, ...] = tuple(
    name
    for name in REQUIRED_RECOVERY_DATABASES
    if name in ACTIVE_DEV_RECOVERY_DATABASES
)

assert len(REQUIRED_RECOVERY_DATABASES) == 7
assert set(REQUIRED_RECOVERY_PRODUCTION) | set(REQUIRED_RECOVERY_DEVELOPMENT) == set(
    REQUIRED_RECOVERY_DATABASES
)


def is_required_recovery_database(name: str) -> bool:
    return name in REQUIRED_RECOVERY_DATABASES


def is_required_recovery_production(name: str) -> bool:
    return name in REQUIRED_RECOVERY_PRODUCTION
