"""Database name classification by role and backup eligibility."""

from enum import Enum

from pydantic import BaseModel

SHARED_AUTHORITY_NAME = "android_permission_intel"
RESTORE_CHECK_PREFIX = "_restorecheck_"
PROD_SUFFIX = "_prod"
DEV_SUFFIX = "_dev"


class DatabaseRole(str, Enum):
    PRODUCTION = "production"
    DEVELOPMENT = "development"
    SHARED_AUTHORITY = "shared_authority"
    RESTORE_CHECK_TEMP = "restore_check_temp"
    UNKNOWN = "unknown"


class DatabaseClassification(BaseModel):
    name: str
    role: DatabaseRole
    backup_source: bool
    dev_target: bool
    manual_review: bool = False
    notes: str = ""


def classify_database(name: str) -> DatabaseClassification:
    """Classify a database by naming convention and platform policy."""
    if name.startswith(RESTORE_CHECK_PREFIX):
        return DatabaseClassification(
            name=name,
            role=DatabaseRole.RESTORE_CHECK_TEMP,
            backup_source=False,
            dev_target=False,
            notes="Temporary restore-check database; not a backup source.",
        )

    if name == SHARED_AUTHORITY_NAME:
        return DatabaseClassification(
            name=name,
            role=DatabaseRole.SHARED_AUTHORITY,
            backup_source=True,
            dev_target=False,
            notes="Shared authority database; backup source per platform policy.",
        )

    if name.endswith(PROD_SUFFIX):
        return DatabaseClassification(
            name=name,
            role=DatabaseRole.PRODUCTION,
            backup_source=True,
            dev_target=False,
            notes="Production source-of-truth; eligible for backup.",
        )

    if name.endswith(DEV_SUFFIX):
        return DatabaseClassification(
            name=name,
            role=DatabaseRole.DEVELOPMENT,
            backup_source=False,
            dev_target=True,
            notes="Development target; excluded from backup (disposable).",
        )

    return DatabaseClassification(
        name=name,
        role=DatabaseRole.UNKNOWN,
        backup_source=False,
        dev_target=False,
        manual_review=True,
        notes="Unknown naming pattern; requires manual review before any action.",
    )


def exclusion_reason(classification: DatabaseClassification) -> str | None:
    """Return a human-readable exclusion reason, or None if backup-eligible."""
    if classification.backup_source:
        return None
    if classification.role == DatabaseRole.DEVELOPMENT:
        return "Development database (*_dev); disposable, not a backup source."
    if classification.role == DatabaseRole.RESTORE_CHECK_TEMP:
        return "Restore-check temp database; not a backup source."
    if classification.manual_review:
        return "Unknown role; manual review required."
    return "Not designated as a backup source."
