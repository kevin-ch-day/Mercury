"""Deterministic Git evidence collection for a prepared temporary capture root."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


GitRunner = Callable[[Path, tuple[str, ...]], str]


def collect_git_evidence(repo: Path, destination: Path, run: GitRunner) -> None:
    """Write required read-only Git receipts using an injected command runner."""
    destination.mkdir(parents=True, exist_ok=True)
    commands = {
        "HEAD": ("rev-parse", "HEAD"),
        "TREE": ("rev-parse", "HEAD^{tree}"),
        "origin_main": ("rev-parse", "origin/main"),
        "branch_vv.txt": ("branch", "-vv"),
        "remotes.txt": ("remote", "-v"),
        "show-ref.txt": ("show-ref",),
        "submodules.txt": ("submodule", "status"),
        "tracked_file_modes.txt": ("ls-tree", "-r", "HEAD"),
        "tracked_ls_tree.txt": ("ls-tree", "-r", "--name-only", "HEAD"),
        "tracked_index_stage.txt": ("ls-files", "--stage"),
        "commit_metadata.txt": ("show", "-s", "--format=fuller", "HEAD"),
        "commit_object.txt": ("cat-file", "-p", "HEAD"),
        "git_status_short.txt": ("status", "--short"),
    }
    for filename, args in commands.items():
        (destination / filename).write_text(run(repo, args) + "\n", encoding="utf-8")
    tracked = run(repo, ("ls-files",)).splitlines()
    (destination / "tracked_file_count.txt").write_text(f"{len(tracked)}\n", encoding="utf-8")
