"""Repository and config paths."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
OUTPUT_DIR = REPO_ROOT / "output"
LOGS_DIR = REPO_ROOT / "logs"

DATABASES_EXAMPLE = CONFIG_DIR / "databases.example.toml"
DATABASES_LOCAL = CONFIG_DIR / "databases.toml"
REPOS_EXAMPLE = CONFIG_DIR / "repos.example.toml"
REPOS_LOCAL = CONFIG_DIR / "repos.toml"
LOCAL_EXAMPLE = CONFIG_DIR / "local.example.toml"
LOCAL_CONFIG = CONFIG_DIR / "local.toml"
PROTECTION_REPORT_FILE = OUTPUT_DIR / "protection_status.txt"
