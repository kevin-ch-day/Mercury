"""Primary/legacy storage root configuration and path derivation.

C1–C2 introduce these models without switching existing Mercury writers.
Until cutover, ``active_write_role`` defaults to ``legacy`` (USB transition source).
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib
from pydantic import BaseModel

from mercury.core.paths import LOCAL_CONFIG
from mercury.core.storage_roles import (
    CONTROL_DIRNAME,
    DEFAULT_FILESYSTEM_TYPE,
    DEFAULT_LEGACY_LABEL,
    DEFAULT_LEGACY_MOUNT,
    DEFAULT_LEGACY_UUID,
    DEFAULT_MIN_FREE_BYTES,
    DEFAULT_MIN_FREE_PERCENT,
    DEFAULT_PRIMARY_LABEL,
    DEFAULT_PRIMARY_MOUNT,
    DEFAULT_PRIMARY_UUID,
    ENV_LEGACY_MOUNT,
    ENV_PRIMARY_MOUNT,
    ENV_USB_MOUNT,
    MAINTENANCE_WRITE_FREEZE_STATES,
    MERCURY_LAYOUT_DIRS,
    STORAGE_SCHEMA_VERSION,
    MigrationState,
    MountValidationCode,
    StorageRootRole,
    StorageWriteRole,
)
from mercury.core.storage_space import SpacePolicy
from mercury.core.storage_validate import MountValidationResult, validate_storage_mount


class StorageIdentityDocument(BaseModel):
    """Schema for ``.mercury_control/storage_identity.json`` on primary."""

    schema_version: str = STORAGE_SCHEMA_VERSION
    storage_role: str
    filesystem_uuid: str
    filesystem_label: str
    mount_path: str
    filesystem_type: str
    device_model: str | None = None
    device_serial: str | None = None
    initialization_timestamp: str | None = None
    mercury_version: str | None = None


@dataclass(frozen=True)
class StorageRootConfig:
    """One configured storage volume."""

    key: str  # "primary" | "legacy"
    role: StorageRootRole
    label: str
    mount_path: Path
    filesystem_uuid: str
    filesystem_type: str
    writable: bool

    def layout_path(self, dirname: str) -> Path:
        return self.mount_path / dirname

    @property
    def backup_root(self) -> Path:
        return self.layout_path("mercury_backups")

    @property
    def log_dir(self) -> Path:
        return self.layout_path("mercury_logs")

    @property
    def manifest_dir(self) -> Path:
        return self.layout_path("mercury_manifests")

    @property
    def runbook_dir(self) -> Path:
        return self.layout_path("mercury_runbooks")

    @property
    def repo_backup_root(self) -> Path:
        return self.layout_path("mercury_repo_backups")

    @property
    def state_dir(self) -> Path:
        return self.layout_path("mercury_state")

    @property
    def control_dir(self) -> Path:
        """Primary-only reserved namespace (never part of migrated payload equality)."""
        return self.mount_path / CONTROL_DIRNAME


@dataclass(frozen=True)
class StorageConfig:
    """Resolved Mercury multi-root storage configuration."""

    primary: StorageRootConfig
    legacy: StorageRootConfig
    active_write_role: StorageWriteRole
    migration_state: MigrationState
    space_policy: SpacePolicy = field(default_factory=SpacePolicy)
    schema_version: str = STORAGE_SCHEMA_VERSION
    source: str = "defaults"
    usb_mount_deprecated_override: bool = False

    @property
    def cutover_complete(self) -> bool:
        """Derived compatibility flag."""
        return self.migration_state in {
            MigrationState.CUTOVER_COMPLETE,
            MigrationState.LEGACY_LOCKED,
        }

    @property
    def active_write_root(self) -> StorageRootConfig:
        if self.active_write_role == StorageWriteRole.PRIMARY:
            return self.primary
        return self.legacy

    def root_for_role(self, role: StorageWriteRole) -> StorageRootConfig:
        return self.primary if role == StorageWriteRole.PRIMARY else self.legacy

    def derived_paths(self) -> dict[str, str]:
        """Flat path map matching historical [mercury] keys for the active writer."""
        root = self.active_write_root
        return {
            "usb_mount": str(root.mount_path),
            "backup_root": str(root.backup_root),
            "log_dir": str(root.log_dir),
            "repo_backup_root": str(root.repo_backup_root),
            "manifest_dir": str(root.manifest_dir),
            "runbook_dir": str(root.runbook_dir),
        }


@dataclass(frozen=True)
class StorageWriteGate:
    """Whether a routine storage write is allowed right now."""

    allowed: bool
    code: MountValidationCode | None
    blocker: str | None
    mount_validation: MountValidationResult | None = None


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_migration_state(raw: object) -> MigrationState:
    text = str(raw or MigrationState.NOT_STARTED).strip()
    try:
        return MigrationState(text)
    except ValueError:
        return MigrationState.NOT_STARTED


def _parse_write_role(raw: object) -> StorageWriteRole:
    text = str(raw or StorageWriteRole.LEGACY).strip()
    try:
        return StorageWriteRole(text)
    except ValueError:
        return StorageWriteRole.LEGACY


def _warn_deprecated_usb_mount() -> None:
    warnings.warn(
        f"{ENV_USB_MOUNT} is deprecated; use {ENV_LEGACY_MOUNT} for the transitional "
        f"USB role and {ENV_PRIMARY_MOUNT} for the canonical primary. "
        f"{ENV_USB_MOUNT} never overrides primary after cutover.",
        DeprecationWarning,
        stacklevel=3,
    )


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if raw and str(raw).strip():
        return Path(str(raw).strip()).expanduser().resolve()
    return None


def default_storage_config() -> StorageConfig:
    """Pre-cutover defaults: USB remains the active writer (transition_source)."""
    primary = StorageRootConfig(
        key="primary",
        role=StorageRootRole.CANONICAL,
        label=DEFAULT_PRIMARY_LABEL,
        mount_path=Path(DEFAULT_PRIMARY_MOUNT),
        filesystem_uuid=DEFAULT_PRIMARY_UUID,
        filesystem_type=DEFAULT_FILESYSTEM_TYPE,
        writable=True,
    )
    legacy = StorageRootConfig(
        key="legacy",
        role=StorageRootRole.TRANSITION_SOURCE,
        label=DEFAULT_LEGACY_LABEL,
        mount_path=Path(DEFAULT_LEGACY_MOUNT),
        filesystem_uuid=DEFAULT_LEGACY_UUID,
        filesystem_type=DEFAULT_FILESYSTEM_TYPE,
        writable=True,
    )
    return StorageConfig(
        primary=primary,
        legacy=legacy,
        active_write_role=StorageWriteRole.LEGACY,
        migration_state=MigrationState.NOT_STARTED,
        space_policy=SpacePolicy(),
        source="defaults",
    )


def _root_from_section(
    *,
    key: str,
    section: dict[str, Any],
    defaults: StorageRootConfig,
) -> StorageRootConfig:
    role_raw = str(section.get("role") or defaults.role.value).strip()
    try:
        role = StorageRootRole(role_raw)
    except ValueError:
        role = defaults.role
    mount = section.get("mount_path") or str(defaults.mount_path)
    uuid = str(section.get("filesystem_uuid") or defaults.filesystem_uuid).strip()
    label = str(section.get("label") or defaults.label).strip()
    fstype = str(section.get("filesystem_type") or defaults.filesystem_type).strip()
    writable = defaults.writable if "writable" not in section else bool(section["writable"])
    return StorageRootConfig(
        key=key,
        role=role,
        label=label,
        mount_path=Path(str(mount).strip()).expanduser(),
        filesystem_uuid=uuid,
        filesystem_type=fstype or DEFAULT_FILESYSTEM_TYPE,
        writable=writable,
    )


def _space_policy_from_section(section: dict[str, Any]) -> SpacePolicy:
    minimum_free_bytes = section.get("minimum_free_bytes", DEFAULT_MIN_FREE_BYTES)
    minimum_free_percent = section.get("minimum_free_percent", DEFAULT_MIN_FREE_PERCENT)
    try:
        bytes_value = int(minimum_free_bytes)
    except (TypeError, ValueError):
        bytes_value = DEFAULT_MIN_FREE_BYTES
    try:
        percent_value = float(minimum_free_percent)
    except (TypeError, ValueError):
        percent_value = DEFAULT_MIN_FREE_PERCENT
    return SpacePolicy(minimum_free_bytes=bytes_value, minimum_free_percent=percent_value)


def _compat_from_mercury_section(mercury: dict[str, Any], base: StorageConfig) -> StorageConfig:
    """Honor existing [mercury].usb_mount / backup_root when [storage] is absent/partial."""
    legacy = base.legacy
    usb_mount = mercury.get("usb_mount")
    backup_root = mercury.get("backup_root")
    mount_path = legacy.mount_path
    if usb_mount and str(usb_mount).strip():
        mount_path = Path(str(usb_mount).strip()).expanduser()
    elif backup_root and str(backup_root).strip():
        backup = Path(str(backup_root).strip()).expanduser()
        if backup.name == "mercury_backups":
            mount_path = backup.parent
    if mount_path == legacy.mount_path:
        return base
    updated_legacy = StorageRootConfig(
        key=legacy.key,
        role=legacy.role,
        label=legacy.label,
        mount_path=mount_path,
        filesystem_uuid=legacy.filesystem_uuid,
        filesystem_type=legacy.filesystem_type,
        writable=legacy.writable,
    )
    return StorageConfig(
        primary=base.primary,
        legacy=updated_legacy,
        active_write_role=base.active_write_role,
        migration_state=base.migration_state,
        space_policy=base.space_policy,
        schema_version=base.schema_version,
        source=f"{base.source}+mercury_compat",
        usb_mount_deprecated_override=base.usb_mount_deprecated_override,
    )


def load_storage_config(
    *,
    local_config: Path | None = None,
    warn_deprecated: bool = True,
) -> StorageConfig:
    """
    Load ``[storage]`` from config/local.toml with backward-compatible defaults.

    Until cutover, missing ``[storage]`` yields legacy USB as ``transition_source``
    and ``active_write_role=legacy``, matching current operational behavior.
    """
    config_path = local_config or LOCAL_CONFIG
    base = default_storage_config()
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            loaded = tomllib.load(handle)
        if isinstance(loaded, dict):
            data = loaded

    mercury = _as_dict(data.get("mercury"))
    storage = _as_dict(data.get("storage"))

    if storage:
        primary = _root_from_section(
            key="primary",
            section=_as_dict(storage.get("primary")),
            defaults=base.primary,
        )
        legacy_section = _as_dict(storage.get("legacy"))
        # Before cutover, default legacy role is transition_source even if omitted.
        if "role" not in legacy_section:
            legacy_section = {**legacy_section, "role": StorageRootRole.TRANSITION_SOURCE.value}
        if "writable" not in legacy_section:
            legacy_section = {**legacy_section, "writable": True}
        legacy = _root_from_section(key="legacy", section=legacy_section, defaults=base.legacy)
        active = _parse_write_role(storage.get("active_write_role", StorageWriteRole.LEGACY))
        migration = _parse_migration_state(storage.get("migration_state", MigrationState.NOT_STARTED))
        # Derived cutover_complete boolean may appear; prefer migration_state.
        if storage.get("cutover_complete") is True and migration == MigrationState.NOT_STARTED:
            migration = MigrationState.CUTOVER_COMPLETE
            active = StorageWriteRole.PRIMARY
            legacy = StorageRootConfig(
                key=legacy.key,
                role=StorageRootRole.LEGACY_ARCHIVE,
                label=legacy.label,
                mount_path=legacy.mount_path,
                filesystem_uuid=legacy.filesystem_uuid,
                filesystem_type=legacy.filesystem_type,
                writable=False,
            )
        space = _space_policy_from_section(_as_dict(storage.get("space_policy")))
        cfg = StorageConfig(
            primary=primary,
            legacy=legacy,
            active_write_role=active,
            migration_state=migration,
            space_policy=space,
            source="config",
        )
    else:
        cfg = _compat_from_mercury_section(mercury, base)

    # Role-specific env overrides.
    deprecated_usb = False
    primary_env = _env_path(ENV_PRIMARY_MOUNT)
    legacy_env = _env_path(ENV_LEGACY_MOUNT)
    usb_env = _env_path(ENV_USB_MOUNT)

    primary = cfg.primary
    legacy = cfg.legacy
    if primary_env is not None:
        primary = StorageRootConfig(
            key=primary.key,
            role=primary.role,
            label=primary.label,
            mount_path=primary_env,
            filesystem_uuid=primary.filesystem_uuid,
            filesystem_type=primary.filesystem_type,
            writable=primary.writable,
        )
    if legacy_env is not None:
        legacy = StorageRootConfig(
            key=legacy.key,
            role=legacy.role,
            label=legacy.label,
            mount_path=legacy_env,
            filesystem_uuid=legacy.filesystem_uuid,
            filesystem_type=legacy.filesystem_type,
            writable=legacy.writable,
        )
    elif usb_env is not None:
        deprecated_usb = True
        if warn_deprecated and os.environ.get("PYTEST_CURRENT_TEST") is None:
            _warn_deprecated_usb_mount()
        # MERCURY_USB_MOUNT resolves only the transitional USB / legacy role.
        # After cutover it must not override primary.
        if not cfg.cutover_complete:
            legacy = StorageRootConfig(
                key=legacy.key,
                role=legacy.role,
                label=legacy.label,
                mount_path=usb_env,
                filesystem_uuid=legacy.filesystem_uuid,
                filesystem_type=legacy.filesystem_type,
                writable=legacy.writable,
            )

    # MERCURY_BACKUP_ROOT remains handled by resolve_backup_root() until writers
    # switch to active_write_root in a later cutover commit.

    return StorageConfig(
        primary=primary,
        legacy=legacy,
        active_write_role=cfg.active_write_role,
        migration_state=cfg.migration_state,
        space_policy=cfg.space_policy,
        schema_version=cfg.schema_version,
        source=cfg.source,
        usb_mount_deprecated_override=deprecated_usb,
    )


def control_namespace_path(primary: StorageRootConfig) -> Path:
    return primary.control_dir


def allowed_destination_only_paths() -> frozenset[str]:
    """Relative paths allowed as destination-only during migration verify."""
    return frozenset({CONTROL_DIRNAME})


def layout_dirnames() -> tuple[str, ...]:
    return MERCURY_LAYOUT_DIRS


def write_freeze_active(config: StorageConfig) -> bool:
    """True when migration maintenance freezes routine writers on both roots."""
    return config.migration_state in MAINTENANCE_WRITE_FREEZE_STATES


def assess_routine_write_permission(
    config: StorageConfig,
    *,
    estimated_operation_bytes: int = 0,
    validate_mount: bool = True,
) -> StorageWriteGate:
    """
    Gate routine Mercury writers using role + migration state (+ optional mount check).

    Does not switch existing callers yet; available for C3 wiring and tests.
    """
    if write_freeze_active(config):
        return StorageWriteGate(
            allowed=False,
            code=MountValidationCode.MIGRATION_WRITE_FREEZE,
            blocker=(
                f"storage writes frozen during migration_state={config.migration_state.value}"
            ),
        )

    root = config.active_write_root
    if not root.writable:
        code = (
            MountValidationCode.LEGACY_WRITE_FORBIDDEN
            if root.key == "legacy"
            else MountValidationCode.ROLE_WRITE_FORBIDDEN
        )
        return StorageWriteGate(
            allowed=False,
            code=code,
            blocker=f"storage root '{root.key}' is not writable under current policy",
        )

    if root.key == "legacy" and root.role == StorageRootRole.LEGACY_ARCHIVE:
        return StorageWriteGate(
            allowed=False,
            code=MountValidationCode.LEGACY_WRITE_FORBIDDEN,
            blocker="legacy archive is read-only; refuse write",
        )

    if root.key == "primary" and not config.cutover_complete:
        # Primary may exist and be mounted, but routine writers stay on legacy
        # until cutover (option B). Migration tooling uses a separate gate later.
        return StorageWriteGate(
            allowed=False,
            code=MountValidationCode.CUTOVER_INCOMPLETE,
            blocker="cutover not complete; routine writes remain on transitional USB",
        )

    if not validate_mount:
        return StorageWriteGate(allowed=True, code=MountValidationCode.OK, blocker=None)

    validation = validate_storage_mount(
        mount_path=root.mount_path,
        expected_uuid=root.filesystem_uuid,
        expected_fstype=root.filesystem_type,
        require_writable=True,
        space_policy=config.space_policy,
        estimated_operation_bytes=estimated_operation_bytes,
    )
    if not validation.ok:
        return StorageWriteGate(
            allowed=False,
            code=validation.code,
            blocker=validation.blocker,
            mount_validation=validation,
        )
    return StorageWriteGate(
        allowed=True,
        code=MountValidationCode.OK,
        blocker=None,
        mount_validation=validation,
    )
