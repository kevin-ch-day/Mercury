"""Storage role and migration-state constants for primary/legacy volumes."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# Sibling layout under each storage mount (unchanged from USB contract).
MERCURY_LAYOUT_DIRS: Final[tuple[str, ...]] = (
    "mercury_backups",
    "mercury_logs",
    "mercury_manifests",
    "mercury_repo_backups",
    "mercury_restore_checks",
    "mercury_runbooks",
    "mercury_state",
)

# Reserved primary-only control namespace (excluded from migration equality).
CONTROL_DIRNAME: Final[str] = ".mercury_control"

# Live-append trees: size/mtime always drifts while writers remain on legacy.
# Mismatches refresh from legacy instead of hard-conflicting.
EPHEMERAL_TOP_DIRS: Final[frozenset[str]] = frozenset({"mercury_logs", "mercury_state"})

# Known device defaults (operator-approved contract).
DEFAULT_PRIMARY_MOUNT: Final[str] = "/mnt/MERCURY_DATA_V2"
DEFAULT_PRIMARY_UUID: Final[str] = "715f29a9-2671-477b-8c8d-515d190addb9"
DEFAULT_PRIMARY_LABEL: Final[str] = "MERCURY_DATA_V2"
DEFAULT_LEGACY_MOUNT: Final[str] = "/mnt/MERCURY_DATA_USB"
DEFAULT_LEGACY_UUID: Final[str] = "e4f0c7fb-132e-4867-9c16-5e4749f5c43a"
DEFAULT_LEGACY_LABEL: Final[str] = "MERCURY_DATA_USB"
DEFAULT_FILESYSTEM_TYPE: Final[str] = "ext4"

# Environment variables (role-specific).
ENV_PRIMARY_MOUNT: Final[str] = "MERCURY_PRIMARY_MOUNT"
ENV_LEGACY_MOUNT: Final[str] = "MERCURY_LEGACY_MOUNT"
ENV_BACKUP_ROOT: Final[str] = "MERCURY_BACKUP_ROOT"
ENV_USB_MOUNT: Final[str] = "MERCURY_USB_MOUNT"  # deprecated; transitional USB only

DEFAULT_MIN_FREE_BYTES: Final[int] = 20 * 1024 * 1024 * 1024
DEFAULT_MIN_FREE_PERCENT: Final[float] = 10.0

STORAGE_SCHEMA_VERSION: Final[str] = "1"


class StorageWriteRole(StrEnum):
    """Which configured root receives routine Mercury writes."""

    PRIMARY = "primary"
    LEGACY = "legacy"


class StorageRootRole(StrEnum):
    """Logical role of a configured storage root."""

    CANONICAL = "canonical"
    TRANSITION_SOURCE = "transition_source"
    LEGACY_ARCHIVE = "legacy_archive"
    LOCAL_STAGING = "local_staging"


class MigrationState(StrEnum):
    """Lifecycle of the USB → primary migration."""

    NOT_STARTED = "not_started"
    PLANNED = "planned"
    COPYING = "copying"
    COPIED = "copied"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    VERIFIED_PENDING_CUTOVER = "verified_pending_cutover"
    CUTOVER_COMPLETE = "cutover_complete"
    LEGACY_LOCKED = "legacy_locked"


# States where routine writers (backup/sync/deploy/handoff/restore/ledger) are frozen.
MAINTENANCE_WRITE_FREEZE_STATES: Final[frozenset[MigrationState]] = frozenset(
    {
        MigrationState.VERIFYING,
        MigrationState.VERIFIED,
        MigrationState.VERIFIED_PENDING_CUTOVER,
    }
)


class MountValidationCode(StrEnum):
    """Machine-readable mount validation outcomes."""

    OK = "ok"
    MOUNT_PATH_MISSING = "mount_path_missing"
    NOT_A_MOUNT = "not_a_mount"
    WRONG_UUID = "wrong_uuid"
    WRONG_FSTYPE = "wrong_fstype"
    READ_ONLY = "read_only"
    INSUFFICIENT_SPACE = "insufficient_space"
    STALE_MOUNTPOINT_CONTENT = "stale_mountpoint_content"
    CUTOVER_INCOMPLETE = "cutover_incomplete"
    LEGACY_WRITE_FORBIDDEN = "legacy_write_forbidden"
    MIGRATION_WRITE_FREEZE = "migration_write_freeze"
    ROLE_WRITE_FORBIDDEN = "role_write_forbidden"
    ACTIVE_ROLE_MISMATCH = "active_role_mismatch"
