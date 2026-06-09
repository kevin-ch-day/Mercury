"""Configuration status (seed: example files only)."""

from mercury.core.execution_policy import load_execution_policy
from mercury.core.platform import detect_platform
from mercury.core.paths import DATABASES_LOCAL, LOCAL_CONFIG, REPOS_LOCAL
from mercury.repo.config import load_repo_definitions
from mercury.database.core import configured_database_names
from mercury.database.mariadb.session import try_load_mariadb_config


def config_status() -> dict[str, str]:
    """Report whether optional local config files exist."""
    from mercury.database import discover_from_config

    inventory = discover_from_config()
    mariadb_cfg = try_load_mariadb_config()
    repos = load_repo_definitions(REPOS_LOCAL if REPOS_LOCAL.exists() else None)
    policy = load_execution_policy()
    platform_info = detect_platform()

    backup_root = str(policy.backup_root) if policy.backup_root else "not configured"
    mode = "operational" if policy.live_execution_allowed() else "seed"

    return {
        "databases.toml": (
            "present" if DATABASES_LOCAL.exists() else "not configured (use databases.example.toml)"
        ),
        "local.toml": (
            "present" if LOCAL_CONFIG.exists() else "not configured (use local.example.toml)"
        ),
        "repos.toml": (
            "present" if REPOS_LOCAL.exists() else "not configured (use repos.example.toml)"
        ),
        "mariadb_config": _mariadb_config_status(mariadb_cfg),
        "known_databases": str(inventory.count),
        "known_repositories": str(len(repos)),
        "connection": inventory.connection,
        "primary_config": inventory.primary_config or "platform/catalog only",
        "backup_root": backup_root,
        "mode": mode,
        "platform_support": platform_info.support_label,
        "dry_run": str(policy.dry_run).lower(),
        "live_actions": str(policy.live_actions_enabled).lower(),
    }


def _mariadb_config_status(cfg) -> str:
    if cfg is None:
        return "not ready — run: mercury config init"
    if cfg.use_client and cfg.unix_socket:
        return f"ready (client/socket: {cfg.unix_socket})"
    if cfg.password_env:
        return f"ready (password via {cfg.password_env})"
    return "ready"


__all__ = ["config_status", "configured_database_names"]
