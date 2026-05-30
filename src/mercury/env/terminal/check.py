"""Compact environment check display for the Mercury menu."""

from __future__ import annotations

from mercury.terminal import screen as display_screen
from mercury.database.mariadb.session import MariaDbServerProbe
from mercury.env.probe import EnvProbeResult


def connection_label(probe: MariaDbServerProbe) -> str:
    user = probe.configured_user or "unknown"
    if probe.current_user:
        user = probe.current_user.split("@", 1)[0]
    host = probe.host or "localhost"
    if host == "127.0.0.1":
        host = "localhost"
    if probe.unix_socket:
        return f"{user}@{host} via {probe.unix_socket}"
    return f"{user}@{host}:{probe.port}"


def build_environment_check_fields(
    env: EnvProbeResult,
    probe: MariaDbServerProbe | None = None,
    *,
    error: str | None = None,
) -> dict[str, object]:
    """Flat status fields for menu option 1 — no database listing."""
    from mercury.core.execution_policy import load_execution_policy

    policy = load_execution_policy()
    fields: dict[str, object] = {
        "python": env.python_version,
        "platform": f"{env.platform_system} {env.platform_release}",
        "dry_run": policy.dry_run,
        "live_actions": policy.live_actions_enabled,
    }
    if policy.live_execution_allowed():
        fields["execution"] = "live actions enabled"
    else:
        fields["execution"] = "dry-run only — edit config/local.toml [mercury] to enable writes"
    if probe is not None and probe.connected:
        fields["connected"] = connection_label(probe)
        fields["version"] = probe.server_version or "unknown"
        if probe.latency_ms is not None:
            fields["latency_ms"] = probe.latency_ms
        if probe.user_database_count is not None:
            fields["databases"] = probe.user_database_count
    elif error:
        fields["connected"] = f"failed — {error}"
    else:
        fields["connected"] = "not configured — run: ./run.sh config init"
    return fields


def print_environment_check(
    env: EnvProbeResult,
    probe: MariaDbServerProbe | None = None,
    *,
    error: str | None = None,
) -> None:
    display_screen.write_fields(
        build_environment_check_fields(env, probe, error=error),
    )
