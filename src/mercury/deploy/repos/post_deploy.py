"""Post-deploy Git checkout normalization for bundle and GitHub clones."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from mercury.deploy.repos.models import RepoDeployCandidate


def _git_run(target: Path, *args: str) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    return completed.returncode, stdout, stderr


def recovery_branch_name() -> str:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"mercury-restored-{day}"


def finalize_deployed_repository(candidate: RepoDeployCandidate) -> str:
    """
    Attach origin, land on a named branch, and return operator guidance.

    Avoids leaving USB bundle restores in detached HEAD without context.
    """
    target = Path(candidate.target_path)
    if not (target / ".git").is_dir():
        return ""

    branch = (candidate.branch or "").strip() or recovery_branch_name()
    ref = (candidate.commit or "").strip() or "HEAD"

    if candidate.remote_url:
        code, url, _ = _git_run(target, "remote", "get-url", "origin")
        if code != 0:
            _git_run(target, "remote", "add", "origin", candidate.remote_url)
        elif url != candidate.remote_url:
            _git_run(target, "remote", "set-url", "origin", candidate.remote_url)

    checkout_code, _, checkout_err = _git_run(target, "checkout", "-B", branch, ref)
    if checkout_code != 0 and ref != "HEAD":
        fallback = recovery_branch_name()
        checkout_code, _, checkout_err = _git_run(target, "checkout", "-B", fallback, ref)
        branch = fallback

    _, short_sha, _ = _git_run(target, "rev-parse", "--short", "HEAD")
    _, remote_url, _ = _git_run(target, "remote", "get-url", "origin")
    remote_label = remote_url or candidate.remote_url or "none"

    if checkout_code != 0:
        return (
            f"Post-deploy: could not create branch {branch} ({checkout_err or 'git checkout failed'}). "
            f"HEAD at {short_sha or 'unknown'}; remote: {remote_label}"
        )

    switch_hint = f"git -C {target} switch {branch}"
    return (
        f"Restored commit {short_sha} on branch {branch}; remote: {remote_label}. "
        f"To work on this checkout: {switch_hint}"
    )
