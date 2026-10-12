"""Read-only inventory of registered worktrees that need separate file capture."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class WorktreeCoverage:
    path: Path
    head: str
    branch: str | None
    exists: bool
    dirty: bool | None
    head_reachable_from_main: bool | None
    configured_for_capture: bool

    @property
    def requires_preservation_review(self) -> bool:
        return (
            not self.exists
            or self.dirty is True
            or self.head_reachable_from_main is False
        )

    @property
    def missing_capture_mapping(self) -> bool:
        return self.requires_preservation_review and not self.configured_for_capture


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def audit_registered_worktrees(
    repo_root: Path,
    *,
    configured_paths: set[Path],
    main_ref: str = "main",
) -> list[WorktreeCoverage]:
    """Show registered worktrees omitted by a capture map, without changing Git.

    A clean non-main commit also needs review because a HEAD-only worktree
    snapshot and a root bundle may not preserve an unreferenced detached HEAD.
    """
    listing = _git(repo_root, "worktree", "list", "--porcelain")
    if listing.returncode != 0:
        raise RuntimeError("Unable to list registered Git worktrees")

    configured = {path.resolve() for path in configured_paths}
    rows: list[WorktreeCoverage] = []
    for block in listing.stdout.strip().split("\n\n"):
        if not block:
            continue
        fields = {}
        for line in block.splitlines():
            if " " in line:
                key, value = line.split(" ", 1)
                fields[key] = value
        if "worktree" not in fields or "HEAD" not in fields:
            raise ValueError("Incomplete Git worktree listing")
        path = Path(fields["worktree"])
        exists = path.is_dir()
        dirty: bool | None = None
        if exists:
            status = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
            if status.returncode != 0:
                raise RuntimeError(f"Unable to inspect registered worktree: {path}")
            dirty = bool(status.stdout)

        ancestry = _git(repo_root, "merge-base", "--is-ancestor", fields["HEAD"], main_ref)
        if ancestry.returncode not in (0, 1):
            raise RuntimeError(f"Unable to compare worktree HEAD with {main_ref}: {path}")
        rows.append(
            WorktreeCoverage(
                path=path,
                head=fields["HEAD"],
                branch=fields.get("branch", "").removeprefix("refs/heads/") or None,
                exists=exists,
                dirty=dirty,
                head_reachable_from_main=ancestry.returncode == 0,
                configured_for_capture=path.resolve() in configured,
            )
        )
    return rows
