"""Rewrite stale repository paths from prior workstation layouts."""

from __future__ import annotations

import os
from pathlib import Path

from mercury.repo.config import RepoDefinition

STALE_HOME_PREFIXES: tuple[str, ...] = (
    "/home/secadmin/Laughlin/",
    "/home/secadmin/",
)


def rewrite_stale_repo_path(path: Path) -> Path:
    """Map legacy secadmin paths to the current operator home when applicable."""
    text = str(path)
    home = Path.home()
    for prefix in STALE_HOME_PREFIXES:
        if text.startswith(prefix):
            suffix = text[len(prefix) :]
            return (home / suffix).expanduser()
    return path.expanduser()


def resolve_effective_repo_path(stored: Path) -> Path:
    """Prefer an existing configured path; otherwise use a rewritten fresh-workstation path."""
    stored = stored.expanduser()
    if stored.exists():
        return stored.resolve()
    rewritten = rewrite_stale_repo_path(stored)
    return rewritten.resolve()


def repo_path_is_stale(stored: Path) -> bool:
    """True when repos.toml still references another workstation's home prefix."""
    text = str(stored.expanduser())
    return any(text.startswith(prefix) for prefix in STALE_HOME_PREFIXES)


def stale_repo_path_detail(stored: Path) -> str | None:
    if not repo_path_is_stale(stored):
        return None
    effective = resolve_effective_repo_path(stored)
    if stored.exists():
        return None
    return f"{stored} → {effective}"


def repo_path_missing_detail(repo: RepoDefinition) -> str | None:
    """Return a doctor-friendly missing-path message, or None when the repo is present."""
    effective = resolve_effective_repo_path(repo.path)
    if effective.exists():
        return None
    if repo_path_is_stale(repo.path):
        return f"{repo.display_name} ({effective}; configured: {repo.path})"
    return f"{repo.display_name} ({repo.path})"


def summarize_stale_repo_paths(repos: list[RepoDefinition]) -> str | None:
    """One-line hint when repos.toml still stores another workstation's home prefix."""
    stale = [detail for repo in repos if (detail := stale_repo_path_detail(repo.path)) is not None]
    if not stale:
        return None
    suffix = " …" if len(stale) > 3 else ""
    return (
        f"Stale repository paths in repos.toml ({len(stale)} rewritten at plan time) — "
        f"run ./run.sh repo init-config --force"
    )


WEB_REPO_PREFIX = "/var/www/html/"


def web_repo_parent_dirs_needing_prep(repos: list[RepoDefinition]) -> list[Path]:
    """Parent directories under /var/www/html that need sudo mkdir/chown before clone."""
    parents: dict[str, Path] = {}
    for repo in repos:
        effective = resolve_effective_repo_path(repo.path)
        text = str(effective)
        if not text.startswith(WEB_REPO_PREFIX):
            continue
        parent = effective.parent
        key = str(parent)
        if key in parents:
            continue
        if parent.exists() and os.access(parent, os.W_OK):
            continue
        parents[key] = parent
    return sorted(parents.values(), key=str)
