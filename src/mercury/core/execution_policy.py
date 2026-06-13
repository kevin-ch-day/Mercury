"""Resolve effective dry-run and live-action policy from defaults, config, and env."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import tomllib

from mercury.core.paths import LOCAL_CONFIG, REPO_ROOT
from mercury.core.platform import detect_platform
from mercury.core.safety import DRY_RUN_ONLY, LIVE_ACTIONS_ENABLED
from mercury.core.usb_mount import (
    DEFAULT_USB_MOUNT,
    resolve_usb_mount,
    usb_mount_is_active,
    usb_mount_label,
)

ENV_DRY_RUN = "MERCURY_DRY_RUN"
ENV_LIVE_ACTIONS = "MERCURY_LIVE_ACTIONS"
ENV_BACKUP_ROOT = "MERCURY_BACKUP_ROOT"
ENV_ALLOW_UNSAFE_BACKUP_ROOT = "MERCURY_ALLOW_UNSAFE_BACKUP_ROOT"
REQUIRED_BACKUP_MOUNT = DEFAULT_USB_MOUNT
MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024


def _disk_usage(path: Path) -> shutil._ntuple_diskusage:
    return shutil.disk_usage(path)


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_mercury_section(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    section = data.get("mercury")
    if isinstance(section, dict):
        return section
    return {}


@dataclass(frozen=True)
class ExecutionPolicy:
    """Effective runtime policy for backup execution."""

    dry_run: bool
    live_actions_enabled: bool
    backup_root: Path
    config_path: Path | None = None
    allow_unsafe_backup_root: bool = False
    usb_mount: Path = REQUIRED_BACKUP_MOUNT

    def backup_root_is_within_repo(self) -> bool:
        try:
            self.backup_root.resolve().relative_to(REPO_ROOT.resolve())
            return True
        except ValueError:
            return False

    def backup_root_is_default_fallback(self) -> bool:
        return self.backup_root.resolve() == (REPO_ROOT / "backups").resolve()

    def backup_root_exists(self) -> bool:
        return self.backup_root.exists() and self.backup_root.is_dir()

    def backup_root_is_under_required_mount(self) -> bool:
        try:
            self.backup_root.resolve().relative_to(self.usb_mount.resolve())
            return True
        except ValueError:
            return False

    def required_mount_is_active(self) -> bool:
        return usb_mount_is_active(self.usb_mount)

    def backup_root_free_bytes(self) -> int | None:
        try:
            return _disk_usage(self.backup_root).free
        except OSError:
            return None

    def backup_root_state(self) -> str:
        if self.backup_root_is_within_repo():
            return "repo-local fallback"
        if not self.backup_root_exists():
            return "missing path"
        if not self.backup_root_is_under_required_mount():
            return "unsafe path"
        if not self.required_mount_is_active():
            return "usb not mounted"
        free_bytes = self.backup_root_free_bytes()
        if free_bytes is None:
            return "free space unknown"
        if free_bytes < MIN_FREE_BYTES:
            return "low free space"
        return "usb-mounted"

    def backup_environment_refusal(self) -> str | None:
        """Environment checks for backup writes (platform, USB, config)."""
        platform_info = detect_platform()
        mount_label = usb_mount_label(self.usb_mount)
        if not platform_info.allows_live_execution:
            if platform_info.is_linux:
                distro = platform_info.distro_name or platform_info.distro_id or "Linux"
                return (
                    f"Mercury backup execution is supported on Fedora and Windows. {distro} was detected. "
                    "Use non-Fedora Linux for seed planning/status only, or run backups on Fedora or Windows."
                )
            return (
                f"Mercury backup execution is supported on Fedora and Windows. {platform_info.system} was detected. "
                "Use this host for seed planning/status only."
            )
        if self.config_path is None:
            return (
                "Backup execution requires config/local.toml. Run: mercury config init "
                f"and set [mercury].backup_root under {mount_label}."
            )
        if self.allow_unsafe_backup_root:
            return None
        resolved = self.backup_root.resolve()
        if self.backup_root_is_within_repo():
            return (
                "Refusing backup execution with a repo-local backup_root: "
                f"{resolved}. Configure [mercury].backup_root under {mount_label}."
            )
        if not self.backup_root_exists():
            return (
                "Refusing backup execution because backup_root does not exist: "
                f"{resolved}. Create {mount_label}/mercury_backups on the USB first."
            )
        if not self.backup_root_is_under_required_mount():
            return (
                "Refusing backup execution because backup_root is not under "
                f"{self.usb_mount}: {resolved}"
            )
        if not self.required_mount_is_active():
            return (
                "Refusing backup execution because the required USB mount is not active: "
                f"{self.usb_mount}"
            )
        free_bytes = self.backup_root_free_bytes()
        if free_bytes is None:
            return (
                "Refusing backup execution because free space could not be determined for "
                f"{resolved}."
            )
        if free_bytes < MIN_FREE_BYTES:
            return (
                "Refusing backup execution because backup_root has insufficient free space: "
                f"{resolved} ({free_bytes} bytes free, requires at least {MIN_FREE_BYTES} bytes / 20 GB)."
            )
        return None

    def backup_execution_allowed(self) -> bool:
        """True when backup writes to USB are permitted for this host."""
        return self.backup_environment_refusal() is None

    def backup_refusal_reason(self) -> str | None:
        return self.backup_environment_refusal()

    def live_execution_allowed(self) -> bool:
        """Destructive or privileged live execution (sync, deploy, restore)."""
        if self.backup_environment_refusal() is not None:
            return False
        return (not self.dry_run) and self.live_actions_enabled

    def refusal_reason(self) -> str | None:
        """Why destructive live execution is blocked (sync, deploy, restore)."""
        env_refusal = self.backup_environment_refusal()
        if env_refusal:
            return env_refusal
        if self.dry_run:
            return "Result: dry-run only; no files were written."
        if not self.live_actions_enabled:
            return (
                "Live actions are disabled. Set [mercury].live_actions_enabled = true "
                f"in config/local.toml or export {ENV_LIVE_ACTIONS}=1."
            )
        return None


def backup_mode_label(policy: ExecutionPolicy) -> str:
    """Short operator label for backup write readiness."""
    if policy.backup_execution_allowed():
        return "writes to USB"
    refusal = policy.backup_environment_refusal()
    if refusal and "repo-local" in refusal:
        return "blocked until USB backup root is configured"
    if refusal and "USB" in refusal:
        return "blocked until USB is mounted"
    if refusal and "config/local.toml" in refusal:
        return "blocked until config is initialized"
    return "blocked until backup environment is ready"


def destructive_ops_label(policy: ExecutionPolicy) -> str:
    """Short operator label for sync/deploy/restore gating."""
    if policy.live_execution_allowed():
        return "enabled with confirmation"
    if policy.dry_run or not policy.live_actions_enabled:
        return "requires live_actions_enabled in config/local.toml"
    return "blocked until environment is ready"


def resolve_backup_root(
    *,
    local_config: Path | None = None,
    env_root: str | None = None,
) -> Path:
    """Resolve backup root directory (absolute path)."""
    env_value = env_root if env_root is not None else os.environ.get(ENV_BACKUP_ROOT)
    if env_value and str(env_value).strip():
        return Path(str(env_value).strip()).expanduser().resolve()

    config_path = local_config or LOCAL_CONFIG
    section = _load_mercury_section(config_path)
    configured = section.get("backup_root")
    if configured and str(configured).strip():
        root = Path(str(configured).strip())
        if root.is_absolute():
            return root.resolve()
        return (REPO_ROOT / root).resolve()

    return (REPO_ROOT / "backups").resolve()


def load_execution_policy(
    *,
    local_config: Path | None = None,
    dry_run_override: bool | None = None,
    live_actions_override: bool | None = None,
    backup_root_override: Path | None = None,
    usb_mount_override: Path | None = None,
) -> ExecutionPolicy:
    """
    Resolve execution policy.

    Precedence (highest last):
    - safety.py module defaults
    - config/local.toml [mercury] dry_run / live_actions_enabled
    - MERCURY_DRY_RUN / MERCURY_LIVE_ACTIONS environment variables
    - explicit overrides (tests)
    """
    config_path = local_config or LOCAL_CONFIG
    section = _load_mercury_section(config_path)

    dry_run = DRY_RUN_ONLY
    live_actions = LIVE_ACTIONS_ENABLED

    if "dry_run" in section:
        dry_run = bool(section["dry_run"])
    if "live_actions_enabled" in section:
        live_actions = bool(section["live_actions_enabled"])

    env_dry = _env_bool(ENV_DRY_RUN)
    if env_dry is not None:
        dry_run = env_dry
    env_live = _env_bool(ENV_LIVE_ACTIONS)
    if env_live is not None:
        live_actions = env_live
    allow_unsafe_backup_root = _env_bool(ENV_ALLOW_UNSAFE_BACKUP_ROOT) is True

    if dry_run_override is not None:
        dry_run = dry_run_override
    if live_actions_override is not None:
        live_actions = live_actions_override

    usb_mount = usb_mount_override or resolve_usb_mount(local_config=config_path)
    backup_root = backup_root_override or resolve_backup_root(local_config=config_path)

    return ExecutionPolicy(
        dry_run=dry_run,
        live_actions_enabled=live_actions,
        backup_root=backup_root,
        config_path=config_path if config_path.exists() else None,
        allow_unsafe_backup_root=allow_unsafe_backup_root,
        usb_mount=usb_mount,
    )
