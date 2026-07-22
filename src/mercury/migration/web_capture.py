"""Safe, explicit capture of dirty web worktrees for workstation migration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any

from mercury.core.usb_mount import assert_operator_storage_path, resolve_operator_mount
from mercury.repo.config import RepoDefinition, RepoSelectionError, load_repo_definitions

WEB_REPO_KEYS = ("erebus_web", "scytaledroid_web")
RUNTIME_NAMES = {".env", "config.php", "database.php", "credentials", "secrets", "token", "private"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


@dataclass(frozen=True)
class WebCaptureResult:
    key: str
    name: str
    path: Path
    snapshot_dir: Path
    fingerprint: str
    executed: bool
    restore_checked: bool = False
    error: str | None = None


def _git(path: Path, *args: str, binary: bool = False) -> bytes:
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True).stdout


def _redact_remote(value: str) -> str:
    # Do not preserve credentials embedded in a remote URL.
    if "://" in value and "@" in value:
        prefix, rest = value.split("://", 1)
        return prefix + "://" + rest.split("@", 1)[1]
    if "@" in value and ":" in value.split("@", 1)[0]:
        return value.split("@", 1)[1]
    return value


def _paths(data: bytes) -> list[str]:
    return [part.decode("utf-8", "surrogateescape") for part in data.split(b"\0") if part]


def _metadata(path: Path, relative: str, classification: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError:
        return {"path": relative, "exists": False, "git_classification": classification}
    return {
        "path": relative, "exists": True, "owner": str(info.st_uid), "group": str(info.st_gid),
        "uid": info.st_uid, "gid": info.st_gid, "mode": oct(stat.S_IMODE(info.st_mode)),
        "size": info.st_size, "mtime": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
        "git_classification": classification,
    }


def _runtime_metadata(repo: Path, ignored: set[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(repo).as_posix()
        lowered = path.name.lower()
        if lowered in RUNTIME_NAMES or any(token in lowered for token in ("secret", "password", "credential", "token", "key")):
            found.append(_metadata(path, relative, "ignored" if relative in ignored else "working_tree"))
    return found


def _is_sensitive_runtime_path(relative: str) -> bool:
    """Whether an untracked path must never enter a migration archive.

    Worktree capture preserves code and ordinary untracked assets. Runtime
    secrets are deliberately inventory-only: their contents must be recreated
    on the receiving host through its secret-management process.
    """
    name = Path(relative).name.lower()
    if name == ".env" or name.startswith(".env.") or name in RUNTIME_NAMES or name in {"id_rsa", "id_ed25519"}:
        return True
    if Path(name).suffix in SENSITIVE_SUFFIXES:
        return True
    return any(token in name for token in ("secret", "password", "credential", "token"))


def _safe_tar_add(archive: tarfile.TarFile, repo: Path, relative: str) -> None:
    source = repo / relative
    try:
        if not source.resolve().is_relative_to(repo.resolve()):
            return
    except OSError:
        return
    if source.is_symlink() or not source.is_file():
        return
    archive.add(source, arcname=relative, recursive=False)


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if member.issym() or member.islnk() or not target.is_relative_to(destination.resolve()):
                raise ValueError(f"unsafe archive member: {member.name}")
        handle.extractall(destination, filter="data")


def _capture_data(
    repo: RepoDefinition, *, include_runtime_metadata: bool = True
) -> tuple[dict[str, Any], bytes, bytes, list[str], list[str]]:
    path = repo.path
    status = _git(path, "status", "--porcelain=v2", "--branch", "-z")
    unstaged = _git(path, "diff", "--binary", "--full-index")
    staged = _git(path, "diff", "--cached", "--binary", "--full-index")
    all_untracked = _paths(_git(path, "ls-files", "--others", "--exclude-standard", "-z"))
    untracked = [relative for relative in all_untracked if not _is_sensitive_runtime_path(relative)]
    excluded_sensitive = sorted(relative for relative in all_untracked if _is_sensitive_runtime_path(relative))
    ignored = _paths(_git(path, "ls-files", "--others", "-i", "--exclude-standard", "-z"))
    branch = _git(path, "branch", "--show-current").decode().strip() or "(detached)"
    commit = _git(path, "rev-parse", "HEAD").decode().strip()
    remotes = _git(path, "remote", "-v").decode("utf-8", "replace").splitlines()
    remote_metadata = [_redact_remote(line) for line in remotes]
    digest = hashlib.sha256()
    for value in (status, unstaged, staged, "\0".join(sorted(all_untracked)).encode()):
        digest.update(value)
        digest.update(b"\0")
    ignored_set = set(ignored)
    manifest = {
        "snapshot_timestamp": datetime.now(timezone.utc).isoformat(), "repository_path": str(path),
        "repository_name": repo.display_name, "repository_key": repo.key, "branch": branch, "head_commit": commit,
        "remote_metadata": remote_metadata, "status_porcelain_v2": status.decode("utf-8", "surrogateescape"),
        "untracked_files": sorted(untracked),
        "excluded_sensitive_untracked": excluded_sensitive,
        "ignored_files": sorted(ignored), "status_fingerprint": digest.hexdigest(),
        "source_metadata": _metadata(path, ".", "repository"),
        # Recursive runtime metadata discovery is useful in a capture artifact,
        # but it must not make routine readiness/fingerprint checks walk a
        # large source tree.
        "runtime_configuration_metadata": _runtime_metadata(path, ignored_set) if include_runtime_metadata else [],
    }
    return manifest, unstaged, staged, untracked, ignored


def selected_web_repositories() -> list[RepoDefinition]:
    return [repo for repo in load_repo_definitions() if repo.migration_scope and repo.key in WEB_REPO_KEYS]


def selected_dirty_repositories(*, keys: set[str] | None = None) -> list[RepoDefinition]:
    """Configured dirty worktrees, optionally limited by stable repo keys."""
    from mercury.repo import inspect_repositories

    configured = load_repo_definitions()
    by_key = {repo.key: repo for repo in configured}
    if keys:
        unknown = sorted(keys - set(by_key))
        excluded = sorted(key for key in keys if key in by_key and not by_key[key].migration_scope)
        if unknown:
            raise RepoSelectionError(f"Unknown repository key(s): {', '.join(unknown)}. Available: {', '.join(sorted(by_key))}")
        if excluded:
            raise RepoSelectionError(f"Repository key(s) excluded from migration scope: {', '.join(excluded)}")
    definitions = [repo for repo in configured if repo.migration_scope]
    by_key = {repo.key: repo for repo in definitions}
    selected: list[RepoDefinition] = []
    for status in inspect_repositories(definitions):
        if keys is not None and status.key not in keys:
            continue
        if status.dirty and status.exists and not status.error and status.key in by_key:
            selected.append(by_key[status.key])
    return selected


def snapshot_status(repo: RepoDefinition) -> tuple[str, bool]:
    """Return ``(state, restore_checked)`` using a fingerprint, never timestamps alone."""
    root = resolve_operator_mount() / "mercury_worktree_snapshots"
    manifests = sorted(root.glob(f"*/{repo.path.name}/snapshot_manifest.json"), reverse=True)
    if not manifests:
        return "missing", False
    try:
        recorded = json.loads(manifests[0].read_text(encoding="utf-8"))
        current, *_ = _capture_data(repo, include_runtime_metadata=False)
        restore = bool((recorded.get("restore_validation") or {}).get("passed"))
        if recorded.get("status_fingerprint") != current.get("status_fingerprint"):
            return "stale", restore
        return "current", restore
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError):
        return "invalid", False


def capture_web_worktrees(*, execute: bool = False, repositories: list[RepoDefinition] | None = None) -> list[WebCaptureResult]:
    """Preview or explicitly snapshot two configured web repos; never changes sources."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = resolve_operator_mount() / "mercury_worktree_snapshots" / stamp
    results: list[WebCaptureResult] = []
    for repo in repositories if repositories is not None else selected_web_repositories():
        try:
            manifest, unstaged, staged, untracked, _ignored = _capture_data(repo)
            destination = root / repo.path.name
            if not execute:
                results.append(WebCaptureResult(repo.key, repo.display_name, repo.path, destination, manifest["status_fingerprint"], False))
                continue
            assert_operator_storage_path(destination, action="worktree capture")
            root.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(root, 0o700)
            destination.mkdir(parents=True, mode=0o700)
            os.chmod(destination, 0o700)
            (destination / "tracked-unstaged.patch").write_bytes(unstaged)
            (destination / "staged.patch").write_bytes(staged)
            for artifact in (destination / "tracked-unstaged.patch", destination / "staged.patch"):
                os.chmod(artifact, 0o600)
            archive = destination / "untracked-files.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                for relative in untracked:
                    _safe_tar_add(handle, repo.path, relative)
            os.chmod(archive, 0o600)
            bundle = destination / "history.bundle"
            subprocess.run(["git", "bundle", "create", str(bundle), "HEAD"], cwd=repo.path, check=True, capture_output=True)
            os.chmod(bundle, 0o600)
            manifest["artifacts"] = {"unstaged_patch": "tracked-unstaged.patch", "staged_patch": "staged.patch", "untracked_archive": archive.name, "history_bundle": bundle.name}
            manifest_path = destination / "snapshot_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(manifest_path, 0o600)
            restore_checked = validate_snapshot(destination)
            manifest["restore_validation"] = {"passed": restore_checked, "validated_at": datetime.now(timezone.utc).isoformat()}
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            results.append(WebCaptureResult(repo.key, repo.display_name, repo.path, destination, manifest["status_fingerprint"], True, restore_checked))
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            results.append(WebCaptureResult(repo.key, repo.display_name, repo.path, root / repo.path.name, "", execute, error=str(exc)))
    return results


def capture_worktrees(*, execute: bool = False, repositories: list[RepoDefinition] | None = None, keys: set[str] | None = None) -> list[WebCaptureResult]:
    """Capture every selected dirty configured repository using web-safe rules."""
    return capture_web_worktrees(
        execute=execute,
        repositories=repositories if repositories is not None else selected_dirty_repositories(keys=keys),
    )


def validate_snapshot(snapshot: Path) -> bool:
    """Validate artifacts in a temporary checkout; source repositories stay untouched."""
    manifest = json.loads((snapshot / "snapshot_manifest.json").read_text(encoding="utf-8"))
    bundle = snapshot / manifest["artifacts"]["history_bundle"]
    with tempfile.TemporaryDirectory(prefix="mercury-web-restore-") as temp:
        checkout = Path(temp) / "checkout"
        subprocess.run(["git", "clone", "--no-checkout", str(bundle), str(checkout)], check=True, capture_output=True)
        subprocess.run(["git", "checkout", manifest["head_commit"]], cwd=checkout, check=True, capture_output=True)
        for patch in ("tracked-unstaged.patch", "staged.patch"):
            content = (snapshot / patch).read_bytes()
            if content:
                subprocess.run(["git", "apply", "--check", str(snapshot / patch)], cwd=checkout, check=True, capture_output=True)
        _safe_extract(snapshot / manifest["artifacts"]["untracked_archive"], checkout / "untracked")
    return True
