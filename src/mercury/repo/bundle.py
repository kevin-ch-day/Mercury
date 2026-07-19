"""Git bundle planning and execution for Mercury repo transfer media."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from pydantic import BaseModel, Field

from mercury.core.usb_mount import assert_operator_storage_path
from mercury.repo.config import RepoBundleSettings
from mercury.repo.status import RepoStatus


class RepoBundleEntry(BaseModel):
    key: str
    display_name: str
    repo_path: Path
    branch: str
    commit: str
    remote_url: str
    dirty: bool
    untracked_count: int
    ahead_count: int | None = None
    behind_count: int | None = None
    planned_bundle_path: Path
    planned_manifest_path: Path
    planned_runbook_path: Path
    bundle_verified: bool = False
    bundle_size_bytes: int | None = None
    executed: bool = False
    error: str | None = None
    pruned_bundle_paths: list[Path] = Field(default_factory=list)
    pruned_manifest_paths: list[Path] = Field(default_factory=list)
    pruned_runbook_paths: list[Path] = Field(default_factory=list)


class RepoBundlePlan(BaseModel):
    generated_at: str
    repo_backup_root: Path
    manifest_dir: Path
    runbook_dir: Path
    planned_index_manifest_path: Path
    planned_index_runbook_path: Path
    entries: list[RepoBundleEntry] = Field(default_factory=list)


def _slug(text: str) -> str:
    lowered = text.strip().lower()
    chars = [c if c.isalnum() else "_" for c in lowered]
    while "__" in (value := "".join(chars)):
        chars = list(value.replace("__", "_"))
    return "".join(chars).strip("_") or "repo"


def _bundle_dirs(settings: RepoBundleSettings, display_name: str, stamp_date: str, timestamp: str) -> tuple[Path, Path, Path]:
    slug = _slug(display_name)
    bundle_dir = settings.repo_backup_root / stamp_date / slug
    manifest_dir = settings.manifest_dir / stamp_date
    runbook_dir = settings.runbook_dir / stamp_date
    return (
        bundle_dir / f"{slug}_{timestamp}.bundle",
        manifest_dir / f"{slug}_{timestamp}.repo_manifest.json",
        runbook_dir / f"{slug}_{timestamp}.restore.md",
    )


def _temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _write_text_atomic(path: Path, content: str) -> None:
    temp_path = _temp_path(path)
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _prune_repo_history(
    plan: RepoBundlePlan,
    entry: RepoBundleEntry,
) -> tuple[list[Path], list[Path], list[Path]]:
    slug = _slug(entry.display_name)
    bundle_pattern = f"*/{slug}/{slug}_*.bundle"
    manifest_pattern = f"*/{slug}_*.repo_manifest.json"
    runbook_pattern = f"*/{slug}_*.restore.md"

    pruned_bundles: list[Path] = []
    pruned_manifests: list[Path] = []
    pruned_runbooks: list[Path] = []

    for candidate in sorted(plan.repo_backup_root.glob(bundle_pattern)):
        if candidate == entry.planned_bundle_path:
            continue
        if candidate.is_file():
            candidate.unlink()
            pruned_bundles.append(candidate)

    for candidate in sorted(plan.manifest_dir.glob(manifest_pattern)):
        if candidate == entry.planned_manifest_path:
            continue
        if candidate.is_file():
            candidate.unlink()
            pruned_manifests.append(candidate)

    for candidate in sorted(plan.runbook_dir.glob(runbook_pattern)):
        if candidate == entry.planned_runbook_path:
            continue
        if candidate.is_file():
            candidate.unlink()
            pruned_runbooks.append(candidate)

    return pruned_bundles, pruned_manifests, pruned_runbooks


def build_repo_bundle_plan(
    statuses: list[RepoStatus],
    settings: RepoBundleSettings,
) -> RepoBundlePlan:
    now = datetime.now(timezone.utc)
    stamp_date = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    index_manifest_path = settings.manifest_dir / stamp_date / f"repo_transfer_manifest_{timestamp}.json"
    index_runbook_path = settings.runbook_dir / stamp_date / f"repo_transfer_runbook_{timestamp}.md"
    entries: list[RepoBundleEntry] = []
    for status in statuses:
        bundle_path, manifest_path, runbook_path = _bundle_dirs(
            settings,
            status.display_name,
            stamp_date,
            timestamp,
        )
        entries.append(
            RepoBundleEntry(
                key=status.key,
                display_name=status.display_name,
                repo_path=status.path,
                branch=status.branch,
                commit=status.commit,
                remote_url=status.remote_url,
                dirty=status.dirty,
                untracked_count=status.untracked_count,
                ahead_count=status.ahead_count,
                behind_count=status.behind_count,
                planned_bundle_path=bundle_path,
                planned_manifest_path=manifest_path,
                planned_runbook_path=runbook_path,
                error=status.error,
            )
        )
    return RepoBundlePlan(
        generated_at=now.isoformat(),
        repo_backup_root=settings.repo_backup_root,
        manifest_dir=settings.manifest_dir,
        runbook_dir=settings.runbook_dir,
        planned_index_manifest_path=index_manifest_path,
        planned_index_runbook_path=index_runbook_path,
        entries=entries,
    )


def _ensure_operator_storage_path(path: Path) -> None:
    """Refuse bundle writes outside the configured active writer mount."""
    assert_operator_storage_path(path)


def _manifest_payload(plan: RepoBundlePlan, entry: RepoBundleEntry) -> dict[str, object]:
    return {
        "repo_key": entry.key,
        "repo_name": entry.display_name,
        "repo_path": str(entry.repo_path),
        "branch": entry.branch,
        "commit": entry.commit,
        "remote_url": entry.remote_url,
        "dirty": entry.dirty,
        "untracked_count": entry.untracked_count,
        "ahead_count": entry.ahead_count,
        "behind_count": entry.behind_count,
        "generated_at": plan.generated_at,
        "bundle_path": str(entry.planned_bundle_path),
        "bundle_verified": entry.bundle_verified,
        "bundle_size_bytes": entry.bundle_size_bytes,
        "manifest_path": str(entry.planned_manifest_path),
        "runbook_path": str(entry.planned_runbook_path),
    }


def _index_manifest_payload(plan: RepoBundlePlan) -> dict[str, object]:
    return {
        "generated_at": plan.generated_at,
        "repo_backup_root": str(plan.repo_backup_root),
        "manifest_dir": str(plan.manifest_dir),
        "runbook_dir": str(plan.runbook_dir),
        "index_runbook_path": str(plan.planned_index_runbook_path),
        "repositories": [
            {
                "repo_key": entry.key,
                "repo_name": entry.display_name,
                "repo_path": str(entry.repo_path),
                "branch": entry.branch,
                "commit": entry.commit,
                "remote_url": entry.remote_url,
                "dirty": entry.dirty,
                "untracked_count": entry.untracked_count,
                "ahead_count": entry.ahead_count,
                "behind_count": entry.behind_count,
                "bundle_path": str(entry.planned_bundle_path),
                "bundle_verified": entry.bundle_verified,
                "bundle_size_bytes": entry.bundle_size_bytes,
                "manifest_path": str(entry.planned_manifest_path),
                "runbook_path": str(entry.planned_runbook_path),
                "error": entry.error,
            }
            for entry in plan.entries
        ],
    }


def _runbook_text(entry: RepoBundleEntry) -> str:
    return "\n".join(
        [
            f"# Restore {entry.display_name}",
            "",
            f"Bundle: {entry.planned_bundle_path}",
            f"Commit: {entry.commit}",
            f"Branch: {entry.branch}",
            f"Remote: {entry.remote_url}",
            "",
            "Restore steps:",
            f"1. git clone {entry.planned_bundle_path} {entry.display_name}",
            f"2. cd {entry.display_name}",
            f"3. git checkout {entry.branch}",
            "",
            "Notes:",
            "- Git bundles capture committed Git history only.",
            "- Dirty working tree changes and untracked files are not included.",
            "- Mercury never commits, pushes, or modifies the source repository.",
            "",
        ]
    )


def _index_runbook_text(plan: RepoBundlePlan) -> str:
    lines = [
        "# Mercury repository transfer runbook",
        "",
        f"Generated: {plan.generated_at}",
        f"Repo backup root: {plan.repo_backup_root}",
        f"Manifest dir: {plan.manifest_dir}",
        f"Runbook dir: {plan.runbook_dir}",
        "",
        "Repositories:",
    ]
    for entry in plan.entries:
        state = "dirty" if entry.dirty else "clean"
        if entry.error:
            state = f"error ({entry.error})"
        lines.extend(
            [
                f"- {entry.display_name}",
                f"  path: {entry.repo_path}",
                f"  branch: {entry.branch}",
                f"  commit: {entry.commit}",
                f"  remote: {entry.remote_url}",
                f"  worktree: {state}",
                f"  bundle: {entry.planned_bundle_path}",
                f"  manifest: {entry.planned_manifest_path}",
                f"  restore note: {entry.planned_runbook_path}",
            ]
        )
    lines.extend(
        [
            "",
            "Notes:",
            "- Git bundles include committed history only.",
            "- Dirty tracked changes and untracked files are not included in bundle contents.",
            "- Mercury does not commit, push, or modify repositories.",
            "",
        ]
    )
    return "\n".join(lines)


def execute_repo_bundle_plan(plan: RepoBundlePlan) -> RepoBundlePlan:
    _ensure_operator_storage_path(plan.repo_backup_root)
    _ensure_operator_storage_path(plan.manifest_dir)
    _ensure_operator_storage_path(plan.runbook_dir)

    for entry in plan.entries:
        if entry.error:
            continue
        entry.planned_bundle_path.parent.mkdir(parents=True, exist_ok=True)
        entry.planned_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        entry.planned_runbook_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_temp = _temp_path(entry.planned_bundle_path)
        manifest_temp = _temp_path(entry.planned_manifest_path)
        runbook_temp = _temp_path(entry.planned_runbook_path)
        try:
            subprocess.run(
                ["git", "bundle", "create", str(bundle_temp), "--all"],
                cwd=entry.repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            if not bundle_temp.exists():
                raise ValueError(f"bundle was not created: {bundle_temp}")
            size_bytes = bundle_temp.stat().st_size
            if size_bytes <= 0:
                raise ValueError(f"bundle is empty: {bundle_temp}")
            subprocess.run(
                ["git", "bundle", "verify", str(bundle_temp)],
                cwd=entry.repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            entry.bundle_size_bytes = size_bytes
            entry.bundle_verified = True
            bundle_temp.replace(entry.planned_bundle_path)
            manifest_temp.write_text(
                json.dumps(_manifest_payload(plan, entry), indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_temp.replace(entry.planned_manifest_path)
            runbook_temp.write_text(
                _runbook_text(entry),
                encoding="utf-8",
            )
            runbook_temp.replace(entry.planned_runbook_path)
        finally:
            for temp_path in (bundle_temp, manifest_temp, runbook_temp):
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass

        pruned_bundles, pruned_manifests, pruned_runbooks = _prune_repo_history(
            plan,
            entry,
        )
        entry.pruned_bundle_paths = pruned_bundles
        entry.pruned_manifest_paths = pruned_manifests
        entry.pruned_runbook_paths = pruned_runbooks
        entry.executed = True
    plan.planned_index_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.planned_index_runbook_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(
        plan.planned_index_manifest_path,
        json.dumps(_index_manifest_payload(plan), indent=2) + "\n",
    )
    _write_text_atomic(
        plan.planned_index_runbook_path,
        _index_runbook_text(plan),
    )
    from mercury.state.ledger import record_repo_bundle_execution, record_repo_bundle_retention

    record_repo_bundle_execution(plan)
    record_repo_bundle_retention(plan)
    return plan
