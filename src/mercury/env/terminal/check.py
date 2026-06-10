"""Operator-focused environment check display for the Mercury menu."""

from __future__ import annotations

from mercury import output
from mercury.core.environment_status import build_environment_status, discover_usb_target
from mercury.core.storage_status import (
    backup_root_filesystem,
    backup_root_free_space_label,
    backup_root_mount_label,
    backup_root_storage_status_label,
)
from mercury.database.mariadb.session import MariaDbServerProbe
from mercury.env.probe import EnvProbeResult
from mercury.core.execution_policy import backup_mode_label, destructive_ops_label, load_execution_policy


def connection_label(probe: MariaDbServerProbe) -> str:
    user = probe.configured_user or "unknown"
    if probe.current_user:
        user = probe.current_user.split("@", 1)[0]
    host = probe.host or "localhost"
    if host == "127.0.0.1":
        host = "localhost"
    return f"{user}@{host}"


def build_environment_check_fields(
    env: EnvProbeResult,
    probe: MariaDbServerProbe | None = None,
    *,
    error: str | None = None,
) -> dict[str, dict[str, object]]:
    """Sectioned operator fields for menu option 1."""
    policy = load_execution_policy()
    env_status = build_environment_status(probe_database=probe is not None or error is not None)
    usb = discover_usb_target()
    fields: dict[str, dict[str, object]] = {
        "Runtime": {
            "Python": env.python_version,
            "Platform": f"{env.platform_system} {env.platform_release}",
            "Platform support": env.platform_support,
            "Config": "config/local.toml" if policy.config_path is not None else "not initialized",
        },
        "Local Config": {
            "local.toml": "present" if env_status.config.local_toml_present else "missing",
            "databases.toml": "present" if env_status.config.databases_toml_present else "missing",
            "repos.toml": "present" if env_status.config.repos_toml_present else "missing",
        },
        "Execution Safety": {
            "Backup mode": backup_mode_label(policy),
            "Sync/deploy/restore": destructive_ops_label(policy),
        },
    }

    mariadb_fields: dict[str, object] = {
        "Client": env_status.mariadb.mariadb_client or "not found",
        "Dump tool": env_status.mariadb.mysqldump_client or "not found",
        "Service": env_status.mariadb.service_state,
        "Socket": "available" if env_status.mariadb.socket_available else "unavailable",
        "Config": "present" if env_status.mariadb.config_present else "missing",
    }
    if probe is not None and probe.connected:
        mariadb_fields["Connection"] = "connected"
        mariadb_fields["User"] = connection_label(probe)
        mariadb_fields["Version"] = probe.server_version or "unknown"
        if probe.unix_socket:
            mariadb_fields["Socket path"] = probe.unix_socket
        if probe.latency_ms is not None:
            mariadb_fields["Latency"] = f"{probe.latency_ms:.2f} ms"
    elif error:
        mariadb_fields["Connection"] = f"failed — {error}"
    elif env_status.mariadb.config_present:
        mariadb_fields["Connection"] = "not probed"
    else:
        mariadb_fields["Connection"] = "not configured — run: ./run.sh config init"
    fields["MariaDB"] = mariadb_fields

    backup_storage = {
        "Target": str(policy.backup_root.resolve()),
        "Mount": backup_root_mount_label(policy),
        "USB detected": (
            f"yes ({usb.mount_path})"
            if usb.mercury_layout_present
            else ("mounted, layout incomplete" if usb.mounted else "no")
        ),
    }
    filesystem = backup_root_filesystem(policy.backup_root)
    if filesystem is not None:
        backup_storage["Filesystem"] = filesystem
    free_space = backup_root_free_space_label(policy)
    if free_space is not None:
        backup_storage["Free space"] = free_space
    backup_storage["Status"] = backup_root_storage_status_label(policy)
    if env_status.primary_setup_blocker:
        backup_storage["Note"] = env_status.primary_setup_blocker
    fields["Backup Storage"] = backup_storage
    return fields


def print_environment_check(
    env: EnvProbeResult,
    probe: MariaDbServerProbe | None = None,
    *,
    error: str | None = None,
) -> None:
    sections = build_environment_check_fields(env, probe, error=error)
    for index, (title, fields) in enumerate(sections.items()):
        if index > 0:
            output.write("")
        output.write(title)
        if "_text" in fields:
            output.write(str(fields["_text"]))
            continue
        for name, value in fields.items():
            output.write(_aligned_field(name, value))


def _aligned_field(name: str, value: object, *, label_width: int = 20) -> str:
    return f"{name:<{label_width}}{value}"
