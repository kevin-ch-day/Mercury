"""Complete-history bundle helpers for a pinned Erebus main commit."""

from __future__ import annotations

import subprocess
from pathlib import Path


def create_complete_bundle(repo: Path, destination: Path, expected_commit: str) -> Path:
    """Create and verify a complete bundle with an explicit main ref."""
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "main"], text=True).strip()
    if head != expected_commit:
        raise ValueError("main does not match the expected commit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "bundle", "create", str(destination), "main"], check=True)
    verify = subprocess.check_output(["git", "bundle", "verify", str(destination)], text=True, stderr=subprocess.STDOUT)
    if expected_commit not in verify:
        raise ValueError("bundle verification did not contain the expected commit")
    return destination


def bundle_heads(bundle: Path) -> str:
    return subprocess.check_output(["git", "bundle", "list-heads", str(bundle)], text=True)
