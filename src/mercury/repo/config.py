"""Configured repository inventory and USB output settings."""

from __future__ import annotations

from pathlib import Path
import tomllib

from pydantic import BaseModel, Field

from mercury.core.execution_policy import REQUIRED_BACKUP_MOUNT
from mercury.core.paths import LOCAL_CONFIG, REPOS_EXAMPLE, REPOS_LOCAL

DEFAULT_REPO_BACKUP_ROOT = REQUIRED_BACKUP_MOUNT / "mercury_repo_backups"
DEFAULT_MANIFEST_DIR = REQUIRED_BACKUP_MOUNT / "mercury_manifests"
DEFAULT_RUNBOOK_DIR = REQUIRED_BACKUP_MOUNT / "mercury_runbooks"


class RepoDefinition(BaseModel):
    key: str
    display_name: str
    path: Path


class RepoSelectionError(ValueError):
    """Raised when the operator asks for unknown configured repositories."""


class RepoBundleSettings(BaseModel):
    repo_backup_root: Path = DEFAULT_REPO_BACKUP_ROOT
    manifest_dir: Path = DEFAULT_MANIFEST_DIR
    runbook_dir: Path = DEFAULT_RUNBOOK_DIR


def _load_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _resolve_repo_config_path(path: Path | None = None) -> Path | None:
    if path is not None:
        return path
    if REPOS_LOCAL.exists():
        return REPOS_LOCAL
    if REPOS_EXAMPLE.exists():
        return REPOS_EXAMPLE
    return None


def load_repo_definitions(path: Path | None = None) -> list[RepoDefinition]:
    config_path = _resolve_repo_config_path(path)
    if config_path is None:
        return []
    data = _load_toml(config_path)
    section = data.get("repos")
    if not isinstance(section, dict):
        return []

    repos: list[RepoDefinition] = []
    for key, raw in section.items():
        if not isinstance(raw, dict):
            continue
        raw_path = raw.get("path")
        if not raw_path:
            continue
        display_name = str(raw.get("display_name") or key)
        repo_path = Path(str(raw_path)).expanduser().resolve()
        repos.append(
            RepoDefinition(
                key=str(key),
                display_name=display_name,
                path=repo_path,
            )
        )
    return repos


def load_repo_bundle_settings(path: Path | None = None) -> RepoBundleSettings:
    config_path = path or LOCAL_CONFIG
    data = _load_toml(config_path)
    section = data.get("mercury")
    if not isinstance(section, dict):
        return RepoBundleSettings()

    return RepoBundleSettings(
        repo_backup_root=Path(
            str(section.get("repo_backup_root") or DEFAULT_REPO_BACKUP_ROOT)
        ).expanduser(),
        manifest_dir=Path(
            str(section.get("manifest_dir") or DEFAULT_MANIFEST_DIR)
        ).expanduser(),
        runbook_dir=Path(
            str(section.get("runbook_dir") or DEFAULT_RUNBOOK_DIR)
        ).expanduser(),
    )


def select_repo_definitions(
    repos: list[RepoDefinition],
    *,
    selected_keys: list[str] | None = None,
) -> list[RepoDefinition]:
    if not selected_keys:
        return repos
    selected = {key.strip().lower() for key in selected_keys}
    matched = [
        repo
        for repo in repos
        if repo.key.lower() in selected or repo.display_name.lower() in selected
    ]
    known = {repo.key.lower() for repo in repos} | {repo.display_name.lower() for repo in repos}
    missing = sorted(item for item in selected if item not in known)
    if missing:
        raise RepoSelectionError(
            "Unknown configured repository selection: "
            + ", ".join(missing)
        )
    return matched
