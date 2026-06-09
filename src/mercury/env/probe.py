"""Environment probe for Mercury seed deployments."""

import platform
import sys

from pydantic import BaseModel, Field

from mercury.config.settings import config_status
from mercury.core.execution_policy import load_execution_policy
from mercury.core.platform import detect_platform
from mercury.core.paths import CONFIG_DIR, OUTPUT_DIR, REPO_ROOT
from mercury.core.safety import MODE_SEED, POLICY_SUMMARY


class EnvProbeResult(BaseModel):
    python_version: str
    platform_system: str
    platform_release: str
    platform_support: str = ""
    repo_root: str
    config_dir: str
    output_dir: str
    mode: str
    dry_run_only: bool
    config_status: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    database_probe: dict[str, object] | None = None


def probe_environment(*, check_database: bool = False, menu: bool = False) -> EnvProbeResult:
    """Collect environment facts; optionally run read-only MariaDB probe."""
    from mercury.database import discover_from_config, try_load_mariadb_config

    inventory = discover_from_config()
    mariadb_ready = try_load_mariadb_config() is not None
    platform_info = detect_platform()

    notes: list[str] = []
    if menu:
        if not mariadb_ready:
            notes.append("No MariaDB config — run: mercury config init")
    else:
        notes = [
            f"Known databases (config/catalog): {inventory.count} — use: mercury db discover [--demo]",
            platform_info.operator_note,
        ]
        if mariadb_ready:
            notes.insert(0, "MariaDB config present — use: mercury db ping (read-only probe)")
        else:
            notes.insert(0, "Mercury seed: no live database connections (config/local.toml not ready).")

    db_probe: dict[str, object] | None = None
    if check_database:
        db_probe = {"status": "see probe output below"}

    policy = load_execution_policy()
    mode = MODE_SEED if policy.dry_run or not policy.live_actions_enabled else "operational"

    return EnvProbeResult(
        python_version=sys.version.split()[0],
        platform_system=platform_info.system,
        platform_release=platform_info.release,
        platform_support=platform_info.support_label,
        repo_root=str(REPO_ROOT),
        config_dir=str(CONFIG_DIR),
        output_dir=str(OUTPUT_DIR),
        mode=mode,
        dry_run_only=policy.dry_run,
        config_status=config_status(),
        notes=notes,
        database_probe=db_probe,
    )


def format_policy_summary() -> str:
    return POLICY_SUMMARY
