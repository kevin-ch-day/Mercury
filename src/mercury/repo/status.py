"""Read-only Git repository inspection for Mercury transfer planning."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel

from mercury.repo.config import RepoDefinition


class RepoStatus(BaseModel):
    key: str
    display_name: str
    path: Path
    exists: bool = True
    git_repo: bool = True
    branch: str = "unknown"
    commit: str = "unknown"
    remote_url: str = "n/a"
    dirty: bool = False
    untracked_count: int = 0
    ahead_count: int | None = None
    behind_count: int | None = None
    error: str | None = None

    @property
    def state_label(self) -> str:
        if self.error:
            return "error"
        return "dirty" if self.dirty else "clean"

    @property
    def upstream_label(self) -> str:
        if self.ahead_count is None or self.behind_count is None:
            return "n/a"
        return f"+{self.ahead_count}/-{self.behind_count}"


class RepoStatusSummary(BaseModel):
    configured: int = 0
    clean: int = 0
    dirty: int = 0
    errors: int = 0
    with_untracked: int = 0


def _git_output(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _inspect_single_repo(repo: RepoDefinition) -> RepoStatus:
    if not repo.path.exists():
        return RepoStatus(
            key=repo.key,
            display_name=repo.display_name,
            path=repo.path,
            exists=False,
            git_repo=False,
            error="path not found",
        )

    try:
        _git_output(repo.path, "rev-parse", "--show-toplevel")
    except subprocess.CalledProcessError:
        return RepoStatus(
            key=repo.key,
            display_name=repo.display_name,
            path=repo.path,
            git_repo=False,
            error="not a git repository",
        )

    status = RepoStatus(
        key=repo.key,
        display_name=repo.display_name,
        path=repo.path,
    )
    try:
        status.commit = _git_output(repo.path, "rev-parse", "HEAD")
        branch = _git_output(repo.path, "branch", "--show-current")
        status.branch = branch or "(detached)"
        try:
            status.remote_url = _git_output(repo.path, "remote", "get-url", "origin") or "n/a"
        except subprocess.CalledProcessError:
            status.remote_url = "n/a"

        porcelain = _git_output(repo.path, "status", "--porcelain=2", "--branch")
        ahead, behind, dirty, untracked = _parse_porcelain_v2(porcelain)
        status.ahead_count = ahead
        status.behind_count = behind
        status.dirty = dirty
        status.untracked_count = untracked
    except subprocess.CalledProcessError as exc:
        status.error = exc.stderr.strip() or exc.stdout.strip() or str(exc)
    return status


def _parse_porcelain_v2(text: str) -> tuple[int | None, int | None, bool, int]:
    ahead: int | None = None
    behind: int | None = None
    dirty = False
    untracked = 0
    for line in text.splitlines():
        if line.startswith("# branch.ab "):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    ahead = int(parts[2].lstrip("+"))
                    behind = int(parts[3].lstrip("-"))
                except ValueError:
                    ahead = None
                    behind = None
            continue
        if line.startswith("? "):
            dirty = True
            untracked += 1
            continue
        if line and not line.startswith("#"):
            dirty = True
    return ahead, behind, dirty, untracked


def inspect_repositories(repos: list[RepoDefinition]) -> list[RepoStatus]:
    return [_inspect_single_repo(repo) for repo in repos]


def summarize_repo_statuses(statuses: list[RepoStatus]) -> RepoStatusSummary:
    summary = RepoStatusSummary(configured=len(statuses))
    for status in statuses:
        if status.error:
            summary.errors += 1
            continue
        if status.dirty:
            summary.dirty += 1
        else:
            summary.clean += 1
        if status.untracked_count > 0:
            summary.with_untracked += 1
    return summary
