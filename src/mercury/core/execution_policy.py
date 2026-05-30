"""Resolve effective dry-run and live-action policy from defaults, config, and env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tomllib

from mercury.core.paths import LOCAL_CONFIG, REPO_ROOT
from mercury.core.safety import DRY_RUN_ONLY, LIVE_ACTIONS_ENABLED

ENV_DRY_RUN = "MERCURY_DRY_RUN"
ENV_LIVE_ACTIONS = "MERCURY_LIVE_ACTIONS"
ENV_BACKUP_ROOT = "MERCURY_BACKUP_ROOT"


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

    def live_execution_allowed(self) -> bool:
        """Live backup execution requires both flags to permit writes."""
        return (not self.dry_run) and self.live_actions_enabled

    def refusal_reason(self) -> str | None:
        if self.live_execution_allowed():
            return None
        if self.dry_run:
            return "Dry-run mode is enabled; backup files will not be written."
        if not self.live_actions_enabled:
            return (
                "Live actions are disabled. Set [mercury].live_actions_enabled = true "
                f"in config/local.toml or export {ENV_LIVE_ACTIONS}=1."
            )
        return "Live backup execution is not permitted by current policy."


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

    if dry_run_override is not None:
        dry_run = dry_run_override
    if live_actions_override is not None:
        live_actions = live_actions_override

    backup_root = backup_root_override or resolve_backup_root(local_config=config_path)

    return ExecutionPolicy(
        dry_run=dry_run,
        live_actions_enabled=live_actions,
        backup_root=backup_root,
        config_path=config_path if config_path.exists() else None,
    )
