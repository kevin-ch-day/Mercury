"""Merge USB repo manifest metadata into workstation repo definitions."""

from __future__ import annotations

from mercury.repo.config import RepoBundleSettings, RepoDefinition, load_repo_bundle_settings
from mercury.repo.manifest_index import latest_repo_manifest_entries


def enrich_repo_definitions_from_usb(
    definitions: list[RepoDefinition],
    *,
    settings: RepoBundleSettings | None = None,
) -> list[RepoDefinition]:
    """Fill missing remote_url/default_branch from newest USB repo manifests."""
    resolved_settings = settings or load_repo_bundle_settings()
    manifests = latest_repo_manifest_entries(resolved_settings.manifest_dir)
    enriched: list[RepoDefinition] = []
    for repo in definitions:
        manifest = manifests.get(repo.key)
        if manifest is None:
            enriched.append(repo)
            continue
        remote = str(manifest.get("remote_url") or "").strip() or None
        branch = str(manifest.get("branch") or repo.default_branch)
        enriched.append(
            repo.model_copy(
                update={
                    "remote_url": repo.remote_url or remote,
                    "default_branch": branch or repo.default_branch,
                }
            )
        )
    return enriched
