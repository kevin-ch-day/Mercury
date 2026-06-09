"""First-run setup detection and layered operator environment status."""

from __future__ import annotations

import getpass
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mercury.core.execution_policy import (
    REQUIRED_BACKUP_MOUNT,
    ExecutionPolicy,
    _path_is_mount,
    load_execution_policy,
)
from mercury.core.path_permissions import PathPermissionCheck
from mercury.core.setup_paths import assess_mercury_path_permissions
from mercury.core.paths import DATABASES_LOCAL, LOCAL_CONFIG, REPOS_LOCAL
from mercury.core.storage_status import backup_root_summary_label
from mercury.database.mariadb.probe import probe_client_tooling

DEFAULT_USB_BACKUP_ROOT = REQUIRED_BACKUP_MOUNT / "mercury_backups"
MERCURY_USB_MARKERS = ("mercury_backups", "mercury_logs")
MARIADB_SOCKET = Path("/var/lib/mysql/mysql.sock")


@dataclass(frozen=True)
class ConfigSetupStatus:
    local_toml_present: bool
    databases_toml_present: bool
    repos_toml_present: bool

    @property
    def initialized(self) -> bool:
        return self.local_toml_present and self.databases_toml_present and self.repos_toml_present

    @property
    def missing_labels(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.local_toml_present:
            missing.append("local.toml")
        if not self.databases_toml_present:
            missing.append("databases.toml")
        if not self.repos_toml_present:
            missing.append("repos.toml")
        return tuple(missing)


@dataclass(frozen=True)
class UsbDiscovery:
    mount_path: Path
    mounted: bool
    mercury_layout_present: bool
    suggested_backup_root: Path | None


@dataclass(frozen=True)
class MariaDbLayerStatus:
    mariadb_client: str | None
    mysqldump_client: str | None
    service_state: str
    socket_available: bool
    config_present: bool
    configured_user: str | None
    connection_works: bool | None
    connection_error: str | None

    @property
    def mariadb_client_found(self) -> bool:
        return bool(self.mariadb_client)

    @property
    def mysqldump_found(self) -> bool:
        return bool(self.mysqldump_client)

    @property
    def service_active(self) -> bool:
        return self.service_state == "active"


@dataclass(frozen=True)
class EnvironmentStatus:
    config: ConfigSetupStatus
    usb: UsbDiscovery
    policy: ExecutionPolicy
    mariadb: MariaDbLayerStatus
    permission_checks: tuple[PathPermissionCheck, ...] = ()
    setup_hints: tuple[str, ...] = ()
    primary_setup_blocker: str | None = None
    repairable_blockers: tuple[str, ...] = ()

    @property
    def has_repairable_blockers(self) -> bool:
        return bool(self.repairable_blockers)


def assess_config_setup() -> ConfigSetupStatus:
    return ConfigSetupStatus(
        local_toml_present=LOCAL_CONFIG.exists(),
        databases_toml_present=DATABASES_LOCAL.exists(),
        repos_toml_present=REPOS_LOCAL.exists(),
    )


def discover_usb_target(*, mount_path: Path = REQUIRED_BACKUP_MOUNT) -> UsbDiscovery:
    mounted = mount_path.exists() and _path_is_mount(mount_path)
    layout_present = mounted and all((mount_path / marker).is_dir() for marker in MERCURY_USB_MARKERS)
    suggested = (mount_path / "mercury_backups") if layout_present else None
    return UsbDiscovery(
        mount_path=mount_path,
        mounted=mounted,
        mercury_layout_present=layout_present,
        suggested_backup_root=suggested,
    )


def _socket_available(path: Path = MARIADB_SOCKET) -> bool:
    try:
        return path.is_socket()
    except OSError:
        return False


def _systemd_service_state(unit: str = "mariadb") -> str:
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    state = proc.stdout.strip()
    return state or "unknown"


def _configured_mariadb_user() -> str | None:
    from mercury.database.mariadb.session import try_load_mariadb_config

    cfg = try_load_mariadb_config()
    return cfg.user if cfg else None


def _probe_configured_connection() -> tuple[bool | None, str | None]:
    from mercury.database.mariadb.errors import MariaDbLiveError
    from mercury.database.mariadb.session import readonly_scalar, try_load_mariadb_config

    cfg = try_load_mariadb_config()
    if cfg is None:
        return None, None
    try:
        readonly_scalar(cfg, "SELECT 1 AS ok")
        return True, None
    except (MariaDbLiveError, OSError) as exc:
        return False, str(exc)


def assess_mariadb_status(*, probe_connection: bool = False) -> MariaDbLayerStatus:
    from mercury.database.mariadb.session import try_load_mariadb_config

    tooling = probe_client_tooling()
    mariadb_client = _first_found_tool(tooling.tools, ("mariadb", "mysql"))
    mysqldump_client = _first_found_tool(tooling.tools, ("mariadb-dump", "mysqldump"))
    cfg = try_load_mariadb_config()
    config_present = cfg is not None
    connection_works: bool | None = None
    connection_error: str | None = None
    if probe_connection and config_present:
        connection_works, connection_error = _probe_configured_connection()
    return MariaDbLayerStatus(
        mariadb_client=mariadb_client,
        mysqldump_client=mysqldump_client,
        service_state=_systemd_service_state(),
        socket_available=_socket_available(),
        config_present=config_present,
        configured_user=cfg.user if cfg else None,
        connection_works=connection_works,
        connection_error=connection_error,
    )


def build_environment_status(*, probe_database: bool = False, self_heal: bool = False) -> EnvironmentStatus:
    config = assess_config_setup()
    usb = discover_usb_target()
    policy = load_execution_policy()
    mariadb = assess_mariadb_status(probe_connection=probe_database)
    permission_checks = tuple(
        assess_mercury_path_permissions(policy=policy, usb=usb, self_heal=self_heal)
    )
    repairable_blockers = _collect_repairable_blockers(
        config=config,
        usb=usb,
        policy=policy,
        mariadb=mariadb,
        permission_checks=permission_checks,
    )
    primary_setup_blocker = _resolve_primary_setup_blocker(
        config=config,
        usb=usb,
        policy=policy,
        mariadb=mariadb,
        permission_checks=permission_checks,
    )
    setup_hints = _build_setup_hints(
        config=config,
        usb=usb,
        primary_setup_blocker=primary_setup_blocker,
        has_repairable=bool(repairable_blockers),
    )
    return EnvironmentStatus(
        config=config,
        usb=usb,
        policy=policy,
        mariadb=mariadb,
        permission_checks=permission_checks,
        setup_hints=setup_hints,
        primary_setup_blocker=primary_setup_blocker,
        repairable_blockers=repairable_blockers,
    )


def config_dashboard_label(config: ConfigSetupStatus, *, styled: bool = True) -> str:
    if config.initialized:
        return "[ok] ready" if styled else "ready"
    if not config.local_toml_present:
        return "[!!] local config missing" if styled else "local config missing"
    missing = ", ".join(config.missing_labels)
    return f"[!!] incomplete ({missing})" if styled else f"incomplete ({missing})"


def mariadb_dashboard_label(status: MariaDbLayerStatus, *, styled: bool = True) -> str:
    ok = "[ok]" if styled else "ok"
    warn = "[!!]" if styled else "warn"
    if status.connection_works is True:
        return f"{ok} connected"
    if not status.config_present:
        if status.mariadb_client_found and status.service_active:
            return f"{warn} service active; config missing"
        if not status.mariadb_client_found:
            return f"{warn} client tools missing"
        if status.service_state == "inactive":
            return f"{warn} service stopped"
        if status.service_active and not status.socket_available:
            return f"{warn} socket unavailable"
        return f"{warn} credentials not configured"
    if status.connection_works is False:
        user = status.configured_user or "configured user"
        detail = _short_connection_error(status.connection_error)
        if "access denied" in detail.lower():
            return f"{warn} auth failed for {user}"
        return f"{warn} connection failed — {detail}"
    if status.config_present and status.connection_works is None:
        return f"{warn} not probed"
    if status.mariadb_client_found and status.service_active:
        return f"{warn} not connected"
    return f"{warn} unavailable"


def backup_target_dashboard_label(
    policy: ExecutionPolicy,
    usb: UsbDiscovery,
    *,
    styled: bool = True,
) -> str:
    if policy.config_path is None:
        if usb.mercury_layout_present:
            return (
                f"[!!] USB detected, not configured"
                if styled
                else "USB detected, not configured"
            )
        target = str(policy.backup_root.resolve())
        return (
            f"[!!] temporary dev fallback — {target}"
            if styled
            else f"temporary dev fallback — {target}"
        )
    return backup_root_summary_label(policy)


def storage_status_dashboard_label(
    policy: ExecutionPolicy,
    *,
    config: ConfigSetupStatus,
    usb: UsbDiscovery,
    permission_checks: tuple[PathPermissionCheck, ...],
    styled: bool = True,
) -> str:
    if any(check.needs_repair for check in permission_checks):
        return "[!!] not writable" if styled else "not writable"
    if not config.local_toml_present:
        return "[!!] setup required" if styled else "setup required"
    if policy.backup_root_state() == "usb-mounted":
        return "[ok] ready" if styled else "ready"
    reason = backup_root_unsafe_reason(policy, config=config, usb=usb)
    prefix = "[!!]" if styled else ""
    return f"{prefix} {reason}" if prefix else reason


def backup_root_unsafe_reason(
    policy: ExecutionPolicy,
    *,
    config: ConfigSetupStatus,
    usb: UsbDiscovery,
) -> str:
    if not config.local_toml_present:
        return "setup required — local config missing; repo backups/ is dev-only"
    state = policy.backup_root_state()
    if state == "repo-local fallback":
        if usb.mercury_layout_present:
            return "USB mounted but backup_root still points at repo"
        return "repo-local path is dev-only, not production protection"
    if state == "usb not mounted":
        return "configured USB backup root is not mounted"
    if state == "missing path":
        return "configured backup_root path does not exist"
    if state == "unsafe path":
        return f"backup_root must be under {REQUIRED_BACKUP_MOUNT}"
    if state == "low free space":
        return "USB backup root has less than 20 GB free"
    return "unsafe"


def resolve_dashboard_blocker(
    *,
    setup_blocker: str | None,
    verified_names: set[str],
    source_names: set[str],
    sync_blocker: str,
    config_initialized: bool,
    deploy_complete: bool = False,
) -> str:
    if setup_blocker:
        return setup_blocker
    if not config_initialized:
        return "Run ./run.sh config init, then ./run.sh doctor"
    if deploy_complete and sync_blocker not in {"None.", ""}:
        text = sync_blocker.lower()
        if "dev target missing" in text:
            return "None — rebuild complete."
    if not verified_names and sync_blocker == "No verified full backups exist yet.":
        return sync_blocker
    if not verified_names:
        return sync_blocker
    if sync_blocker in {"None.", ""}:
        return "None."
    return sync_blocker


def recommended_next_step(env: EnvironmentStatus) -> str:
    if not env.config.local_toml_present:
        return "./run.sh config init"
    if env.repairable_blockers:
        return "./run.sh doctor --repair-plan"
    if env.mariadb.connection_works is False:
        return "./run.sh doctor --repair-plan"
    if env.primary_setup_blocker:
        return "./run.sh doctor"
    return "./run.sh menu"


def _collect_repairable_blockers(
    *,
    config: ConfigSetupStatus,
    usb: UsbDiscovery,
    policy: ExecutionPolicy,
    mariadb: MariaDbLayerStatus,
    permission_checks: tuple[PathPermissionCheck, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if config.missing_labels:
        blockers.append("local config not initialized")
    if not usb.mounted and not usb.mercury_layout_present:
        blockers.append("USB backup mount not detected")
    for check in permission_checks:
        if check.needs_repair:
            blockers.append(f"{check.label} not writable")
    if mariadb.service_state == "inactive":
        blockers.append("MariaDB service inactive")
    if mariadb.config_present and mariadb.connection_works is False:
        blockers.append("MariaDB configured user cannot connect")
    elif not mariadb.config_present and config.local_toml_present and mariadb.service_active:
        blockers.append("MariaDB credentials not configured")
    return tuple(blockers)


def _resolve_primary_setup_blocker(
    *,
    config: ConfigSetupStatus,
    usb: UsbDiscovery,
    policy: ExecutionPolicy,
    mariadb: MariaDbLayerStatus,
    permission_checks: tuple[PathPermissionCheck, ...],
) -> str | None:
    if not config.local_toml_present:
        if usb.mercury_layout_present:
            return "Local config not initialized — USB target detected at /mnt/MERCURY_DATA_USB."
        return "Local config not initialized — run: ./run.sh config init."

    for check in permission_checks:
        if check.needs_repair:
            return f"USB Mercury paths not writable by {getpass.getuser()} — {check.label}."

    if policy.backup_root_is_within_repo() and usb.mercury_layout_present:
        return "USB target detected but not configured — set backup_root in config/local.toml."

    if mariadb.service_state == "inactive" and mariadb.mariadb_client_found:
        return "MariaDB service is not running."

    if not mariadb.config_present and config.local_toml_present:
        return None

    if mariadb.connection_works is False:
        user = mariadb.configured_user or "configured user"
        detail = _short_connection_error(mariadb.connection_error)
        return f"MariaDB auth failed for {user} — {detail}."

    return None


def _build_setup_hints(
    *,
    config: ConfigSetupStatus,
    usb: UsbDiscovery,
    primary_setup_blocker: str | None,
    has_repairable: bool,
) -> tuple[str, ...]:
    if config.initialized and primary_setup_blocker is None and not has_repairable:
        return ()
    hints: list[str] = []
    if not config.local_toml_present:
        hints.append("Run: ./run.sh config init")
    elif config.missing_labels:
        hints.append(f"Run: ./run.sh config init  (missing: {', '.join(config.missing_labels)})")
    if has_repairable:
        hints.append("Run: ./run.sh doctor --repair-plan")
    if usb.mercury_layout_present and (not config.local_toml_present or primary_setup_blocker):
        hints.append(f"USB backup layout detected at {usb.mount_path}.")
    return tuple(hints)


def _first_found_tool(tools: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        path = tools.get(name)
        if path and path != "not found":
            return path
    return None


def _short_connection_error(error: str | None) -> str:
    if not error:
        return "connection failed"
    if "Access denied" in error:
        return "access denied for configured user"
    if len(error) > 72:
        return error[:69] + "..."
    return error
