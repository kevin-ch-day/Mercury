"""Repository and config paths."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
OUTPUT_DIR = REPO_ROOT / "output"
LOGS_DIR = REPO_ROOT / "logs"
DATA_DIR = REPO_ROOT / "data"

DATABASES_EXAMPLE = CONFIG_DIR / "databases.example.toml"
DATABASES_LOCAL = CONFIG_DIR / "databases.toml"
REPOS_EXAMPLE = CONFIG_DIR / "repos.example.toml"
REPOS_LOCAL = CONFIG_DIR / "repos.toml"
LOCAL_EXAMPLE = CONFIG_DIR / "local.example.toml"
LOCAL_CONFIG = CONFIG_DIR / "local.toml"
PROTECTION_REPORT_FILE = OUTPUT_DIR / "protection_status.txt"

# Tests/CI can point loaders at a missing or temp local.toml without hiding the
# real operator file on disk (see MERCURY_LOCAL_CONFIG).
ENV_LOCAL_CONFIG = "MERCURY_LOCAL_CONFIG"


def resolve_local_config() -> Path:
    """Return operator ``local.toml``, honoring ``MERCURY_LOCAL_CONFIG`` when set."""
    override = os.environ.get(ENV_LOCAL_CONFIG, "").strip()
    if override:
        return Path(override)
    return LOCAL_CONFIG
