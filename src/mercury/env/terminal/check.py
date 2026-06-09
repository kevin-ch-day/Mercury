"""Operator-focused environment check display for the Mercury menu."""

from __future__ import annotations

from mercury import output
from mercury.core.storage_status import (
    backup_root_filesystem,
    backup_root_free_space_label,
    backup_root_mount_label,
    backup_root_storage_status_label,
)
from mercury.database.mariadb.session import MariaDbServerProbe
from mercury.env.probe import EnvProbeResult


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
    from mercury.core.execution_policy import load_execution_policy

    policy = load_execution_policy()
    fields: dict[str, dict[str, object]] = {
        "Runtime": {
            "Python": env.python_version,
            "Platform": f"{env.platform_system} {env.platform_release}",
            "Platform support": env.platform_support,
            "Config": "config/local.toml" if policy.config_path is not None else "fallback/default",
        },
        "Execution Safety": {
            "Mode": "DRY RUN" if policy.dry_run else "LIVE",
            "Live actions": "disabled" if not policy.live_actions_enabled else "enabled",
            "Destructive sync": (
                "blocked"
                if policy.dry_run or not policy.live_actions_enabled
                else "enabled for ready pairs with SYNC DEV confirmation"
            ),
        },
    }

    mariadb_fields: dict[str, object]
    if probe is not None and probe.connected:
        mariadb_fields = {
            "Status": "connected",
            "User": connection_label(probe),
            "Version": probe.server_version or "unknown",
        }
        if probe.unix_socket:
            mariadb_fields["Socket"] = probe.unix_socket
        if probe.latency_ms is not None:
            mariadb_fields["Latency"] = f"{probe.latency_ms:.2f} ms"
    elif error:
        mariadb_fields = {"Status": f"unavailable — {error}"}
    else:
        mariadb_fields = {"Status": "not configured"}
    fields["MariaDB"] = mariadb_fields

    backup_storage = {
        "Target": str(policy.backup_root.resolve()),
        "Mount": backup_root_mount_label(policy),
    }
    filesystem = backup_root_filesystem(policy.backup_root)
    if filesystem is not None:
        backup_storage["Filesystem"] = filesystem
    free_space = backup_root_free_space_label(policy)
    if free_space is not None:
        backup_storage["Free space"] = free_space
    backup_storage["Status"] = backup_root_storage_status_label(policy)
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
