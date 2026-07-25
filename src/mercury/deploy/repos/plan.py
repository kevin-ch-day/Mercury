"""Plan shell commands for repository deployment."""

from __future__ import annotations

import os
from pathlib import Path

from mercury.deploy.repos.models import RepoDeployCandidate, RepoDeployOptions


def _parent_writable(parent: Path) -> bool:
    if parent.exists():
        return os.access(parent, os.W_OK)
    probe = parent
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    return probe.exists() and os.access(probe, os.W_OK)


def planned_repo_commands(
    candidate: RepoDeployCandidate,
    *,
    options: RepoDeployOptions,
) -> tuple[list[str], str | None]:
    if candidate.exists_on_system:
        if options.skip_existing:
            return [], candidate.skip_reason or f"Repository exists at {candidate.target_path}"
        # Explicit resume mode is limited to a bundle candidate with an exact
        # recorded commit.  It never reclones or deletes an existing worktree;
        # it verifies the pinned object, checks it out, and repairs origin.
        if candidate.source == "usb_bundle" and candidate.bundle_path and candidate.commit:
            from mercury.deploy.repos.post_deploy import recovery_branch_name

            branch = candidate.branch or recovery_branch_name()
            commands = [
                f"git bundle verify {candidate.bundle_path}",
                f"git -C {candidate.target_path} cat-file -e {candidate.commit}^{{commit}}",
                f"git -C {candidate.target_path} checkout -B {branch} {candidate.commit}",
            ]
            if candidate.remote_url:
                commands.append(
                    f"git -C {candidate.target_path} remote set-url origin {candidate.remote_url}"
                )
            return commands, None
        return [], "Existing repository resume requires a verified USB bundle with an exact commit"

    target = Path(candidate.target_path)
    parent = target.parent
    if not _parent_writable(parent):
        return [], f"Parent directory not writable: {parent} (may require sudo mkdir/chown)"

    commands: list[str] = []

    if candidate.source == "github" and candidate.remote_url:
        commands.append(f"mkdir -p {parent}")
        branch = candidate.branch or "main"
        commands.append(f"git clone --branch {branch} {candidate.remote_url} {target}")
        ref = candidate.commit or "HEAD"
        commands.append(f"git -C {target} checkout -B {branch} {ref}")
        if candidate.remote_url:
            commands.append(f"git -C {target} remote set-url origin {candidate.remote_url}")
        return commands, None

    if candidate.source == "usb_bundle" and candidate.bundle_path:
        from mercury.deploy.repos.post_deploy import recovery_branch_name

        commands.append(f"mkdir -p {parent}")
        commands.append(f"git bundle verify {candidate.bundle_path}")
        commands.append(f"git clone {candidate.bundle_path} {target}")
        branch = candidate.branch or recovery_branch_name()
        ref = candidate.commit or "HEAD"
        commands.append(f"git -C {target} checkout -B {branch} {ref}")
        if candidate.remote_url:
            # git clone <bundle> creates origin pointing at the local bundle;
            # replace it with the manifest-pinned official remote.
            commands.append(f"git -C {target} remote set-url origin {candidate.remote_url}")
        return commands, None

    return [], candidate.skip_reason or "No deployment source resolved"
