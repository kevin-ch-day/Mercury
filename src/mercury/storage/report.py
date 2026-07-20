"""Observe-only storage status reporting (C3). Does not switch writers."""

from __future__ import annotations

from dataclasses import dataclass

from mercury.core.storage_roots import StorageConfig, load_storage_config
from mercury.core.storage_roles import MigrationState, StorageRootRole, StorageWriteRole
from mercury.core.storage_validate import MountValidationResult, validate_storage_mount


@dataclass(frozen=True)
class StorageRootStatus:
    key: str
    role: str
    label: str
    mount_path: str
    filesystem_uuid: str
    writable_policy: bool
    validation: MountValidationResult
    is_active_writer: bool

    @property
    def status_tag(self) -> str:
        if self.validation.ok:
            return "[ok]"
        code = self.validation.code.value
        if code in {"not_a_mount", "stale_mountpoint_content", "mount_path_missing"}:
            return "[!!]"
        if code in {"wrong_uuid", "wrong_fstype", "read_only", "insufficient_space"}:
            return "[!!]"
        return "[--]"

    def one_line(self) -> str:
        detail = self.validation.blocker or (
            "mounted and writable" if self.validation.ok else self.validation.code.value
        )

    @property
    def physical_mount_mode(self) -> str:
        """Best-effort kernel mount mode, independent of Mercury policy."""
        options = self.validation.identity.mount_options.split(",")
        if "ro" in options:
            return "read-only"
        if "rw" in options:
            return "read-write"
        return "unknown"
        active = " · ACTIVE WRITER" if self.is_active_writer else ""
        return (
            f"{self.status_tag} {self.label} ({self.role}) @ {self.mount_path} — {detail}{active}"
        )


@dataclass(frozen=True)
class StorageStatusReport:
    config: StorageConfig
    primary: StorageRootStatus
    legacy: StorageRootStatus

    @property
    def active_write_role(self) -> StorageWriteRole:
        return self.config.active_write_role

    @property
    def migration_state(self) -> MigrationState:
        return self.config.migration_state

    def next_step(self) -> str:
        """Operator next action for migration (observe-only guidance)."""
        state = self.migration_state
        if state == MigrationState.VERIFYING:
            return "storage migrate-verify --update-state"
        if state in {
            MigrationState.VERIFIED,
            MigrationState.VERIFIED_PENDING_CUTOVER,
        }:
            return "storage cutover-readiness  # cutover approve not enabled yet"
        if not self.primary.validation.ok:
            return "storage validate / mount primary"
        if state == MigrationState.COPYING:
            return "storage migrate-run --execute  # resume copy"
        if state == MigrationState.COPIED:
            return "storage migrate-verify --update-state"
        if state == MigrationState.PLANNED:
            return "storage migrate-run"
        if self.legacy.validation.ok and self.primary.validation.ok:
            return "storage migrate-plan"
        return "storage status"

    def dashboard_line(self) -> str:
        """Compact main-menu line: active writer + primary readiness."""
        writer = "legacy" if self.active_write_role.value == "legacy" else "primary"
        primary_tag = self.primary.status_tag
        if self.primary.validation.ok:
            primary_detail = "mounted"
        elif self.primary.validation.identity.stale_mountpoint_entries:
            primary_detail = "unmounted (stale mount-point content)"
        elif not self.primary.validation.identity.path_exists:
            primary_detail = "path missing"
        elif not self.primary.validation.identity.is_mount:
            primary_detail = "not mounted"
        else:
            primary_detail = self.primary.validation.code.value
        next_step = self.next_step()
        # Keep dashboard compact: short token after primary status.
        short = next_step.split(" — ")[0].split("  #")[0]
        if short.startswith("storage "):
            short = short.removeprefix("storage ")
        return (
            f"writer={writer} · migration={self.migration_state.value} · "
            f"primary {primary_tag} {primary_detail} · next={short}"
        )

    def warning_lines(self) -> list[str]:
        warnings: list[str] = []
        if self.primary.validation.identity.stale_mountpoint_entries:
            entries = ", ".join(self.primary.validation.identity.stale_mountpoint_entries[:5])
            warnings.append(
                f"Primary mount point {self.primary.mount_path} is not mounted and contains "
                f"unexpected entries ({entries}). Mercury will not write there or remove them."
            )
        if (
            self.config.cutover_complete
            and self.legacy.role == StorageRootRole.LEGACY_ARCHIVE.value
            and self.legacy.writable_policy
        ):
            warnings.append(
                "Legacy root is marked archive but still writable in config — set writable=false."
            )
        if (
            self.config.cutover_complete
            and self.legacy.role == StorageRootRole.LEGACY_ARCHIVE.value
            and self.legacy.physical_mount_mode == "read-write"
        ):
            warnings.append(
                "Legacy USB archive is physically mounted read-write — Mercury blocks its writes, "
                "but other processes can still modify it. Remount it read-only before transport."
            )
        if self.config.usb_mount_deprecated_override:
            warnings.append(
                "MERCURY_USB_MOUNT is set (deprecated). Prefer MERCURY_LEGACY_MOUNT / MERCURY_PRIMARY_MOUNT."
            )
        if self.migration_state in {
            MigrationState.VERIFYING,
            MigrationState.VERIFIED,
            MigrationState.VERIFIED_PENDING_CUTOVER,
        }:
            warnings.append(
                f"migration_state={self.migration_state.value}: routine storage writes are frozen "
                "until cutover (or state reset)."
            )
        return warnings


def _root_status(config: StorageConfig, *, key: str) -> StorageRootStatus:
    root = config.primary if key == "primary" else config.legacy
    # Observe-only: do not require writable for status display of archive/read views.
    require_writable = root.writable and key == config.active_write_role.value
    validation = validate_storage_mount(
        mount_path=root.mount_path,
        expected_uuid=root.filesystem_uuid,
        expected_fstype=root.filesystem_type,
        require_writable=require_writable,
        space_policy=config.space_policy,
    )
    return StorageRootStatus(
        key=key,
        role=root.role.value,
        label=root.label,
        mount_path=str(root.mount_path),
        filesystem_uuid=root.filesystem_uuid,
        writable_policy=root.writable,
        validation=validation,
        is_active_writer=(key == config.active_write_role.value),
    )


def build_storage_status_report(*, local_config=None) -> StorageStatusReport:
    config = load_storage_config(local_config=local_config, warn_deprecated=True)
    return StorageStatusReport(
        config=config,
        primary=_root_status(config, key="primary"),
        legacy=_root_status(config, key="legacy"),
    )


def suggested_primary_fstab_line(config: StorageConfig | None = None) -> str:
    cfg = config or load_storage_config(warn_deprecated=False)
    return (
        f"UUID={cfg.primary.filesystem_uuid} {cfg.primary.mount_path} "
        f"{cfg.primary.filesystem_type} defaults,nofail,x-systemd.device-timeout=10s 0 2"
    )
