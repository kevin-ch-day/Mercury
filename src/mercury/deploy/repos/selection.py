"""Resolve repository deployment candidates from config and USB manifests."""

from __future__ import annotations

from pathlib import Path

from mercury.repo.config import RepoDefinition, load_repo_bundle_settings, load_repo_definitions
from mercury.repo.manifest_index import latest_repo_manifest_entries
from mercury.repo.path_repair import resolve_effective_repo_path, stale_repo_path_detail
from mercury.repo.status import inspect_repositories
from mercury.deploy.repos.models import RepoDeployCandidate, RepoDeploySource


def _resolve_source(
    repo: RepoDefinition,
    *,
    usb_manifest: dict[str, object] | None,
    prefer_usb_bundle: bool,
) -> tuple[RepoDeploySource, str | None, str | None, str, str | None]:
    bundle_path: str | None = None
    remote_url = repo.remote_url
    branch = repo.default_branch
    commit: str | None = None

    if usb_manifest:
        bundle_path = str(usb_manifest.get("bundle_path") or "").strip() or None
        branch = str(usb_manifest.get("branch") or branch)
        commit = str(usb_manifest.get("commit") or "") or None
        remote = str(usb_manifest.get("remote_url") or "").strip()
        if remote and not remote_url:
            remote_url = remote

    if prefer_usb_bundle and bundle_path and Path(bundle_path).is_file():
        return "usb_bundle", remote_url, bundle_path, branch, commit
    if remote_url:
        return "github", remote_url, bundle_path, branch, commit
    if bundle_path and Path(bundle_path).is_file():
        return "usb_bundle", remote_url, bundle_path, branch, commit
    return "none", remote_url, bundle_path, branch, commit


def resolve_repo_deploy_candidates(
    *,
    repos: list[RepoDefinition] | None = None,
    selected_keys: list[str] | None = None,
    prefer_usb_bundle: bool = False,
    source_mode: str = "auto",
) -> list[RepoDeployCandidate]:
    definitions = repos or load_repo_definitions()
    if selected_keys:
        from mercury.repo.config import select_repo_definitions

        definitions = select_repo_definitions(definitions, selected_keys=selected_keys)

    settings = load_repo_bundle_settings()
    usb_manifests = latest_repo_manifest_entries(settings.manifest_dir)
    effective_definitions = [
        repo.model_copy(update={"path": resolve_effective_repo_path(repo.path)}) for repo in definitions
    ]
    statuses = {status.key: status for status in inspect_repositories(effective_definitions)}
    candidates: list[RepoDeployCandidate] = []

    for repo, effective in zip(definitions, effective_definitions, strict=True):
        status = statuses.get(repo.key)
        exists = bool(status and status.exists and status.git_repo and not status.error)
        usb_manifest = usb_manifests.get(repo.key)
        source, remote_url, bundle_path, branch, commit = _resolve_source(
            repo,
            usb_manifest=usb_manifest,
            prefer_usb_bundle=prefer_usb_bundle or source_mode == "usb",
        )
        if source_mode == "github":
            source = "github" if remote_url else "none"
        elif source_mode == "usb":
            source = "usb_bundle" if bundle_path and Path(bundle_path).is_file() else "none"

        configured_path = str(repo.path)
        effective_path = str(effective.path)
        path_note = stale_repo_path_detail(repo.path)

        candidate = RepoDeployCandidate(
            key=repo.key,
            display_name=repo.display_name,
            target_path=effective_path,
            configured_path=configured_path if path_note else None,
            source=source,
            remote_url=remote_url,
            bundle_path=bundle_path,
            branch=branch,
            commit=commit,
            exists_on_system=exists,
        )
        if exists:
            candidate.skip_reason = f"Repository already present at {effective_path}"
        elif source == "none":
            candidate.skip_reason = "No GitHub remote_url or verified USB bundle available"
        candidates.append(candidate)
    return candidates
