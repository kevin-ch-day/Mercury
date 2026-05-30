"""Environment probe for Mercury seed deployments."""

import platform
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from mercury.config import config_status
from mercury.paths import CONFIG_DIR, OUTPUT_DIR, REPO_ROOT
from mercury.safety import DRY_RUN_ONLY, MODE_SEED, POLICY_SUMMARY


class EnvProbeResult(BaseModel):
    python_version: str
    platform_system: str
    platform_release: str
    repo_root: str
    config_dir: str
    output_dir: str
    mode: str
    dry_run_only: bool
    config_status: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def probe_environment() -> EnvProbeResult:
    """Collect environment facts without connecting to databases."""
    from mercury.database import discover_from_config

    inventory = discover_from_config()
    notes = [
        "Mercury seed: no live database connections.",
        f"Known databases (config/catalog): {inventory.count} — use: mercury db discover [--demo]",
        "Windows may be used for development; Fedora is the production target.",
    ]
    if platform.system() == "Windows":
        notes.append("Running on Windows — expect path and tooling differences on Fedora.")
    elif platform.system() == "Linux":
        notes.append("Linux detected — closer to Fedora production target.")

    return EnvProbeResult(
        python_version=sys.version.split()[0],
        platform_system=platform.system(),
        platform_release=platform.release(),
        repo_root=str(REPO_ROOT),
        config_dir=str(CONFIG_DIR),
        output_dir=str(OUTPUT_DIR),
        mode=MODE_SEED,
        dry_run_only=DRY_RUN_ONLY,
        config_status=config_status(),
        notes=notes,
    )


def format_policy_summary() -> str:
    return POLICY_SUMMARY
