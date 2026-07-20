"""Preview-first, independent offline Git worktree copies on operator storage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from pydantic import BaseModel, Field

from mercury.core.artifact_permissions import ensure_private_directory, restrict_artifact_file
from mercury.core.usb_mount import assert_operator_storage_path, resolve_operator_mount
from mercury.repo.status import RepoStatus


OFFLINE_CLONE_DIRNAME = "mercury_repo_clones"
OFFLINE_SYNC_CONFIRMATION = "SYNC OFFLINE REPOS"
OFFLINE_SYNC_RECEIPT_NAME = "offline_repo_sync_receipt.json"


class OfflineCloneEntry(BaseModel):
    key: str
    display_name: str
    source_path: Path
    destination_path: Path
    commit: str
    source_dirty: bool
    action: str
    error: str | None = None
    executed: bool = False


class OfflineClonePlan(BaseModel):
    root: Path
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    entries: list[OfflineCloneEntry] = Field(default_factory=list)
    receipt_path: Path | None = None


def offline_clone_root() -> Path:
    """Return the HDD/operator-storage location for runnable repository copies."""
    return resolve_operator_mount() / OFFLINE_CLONE_DIRNAME


def offline_sync_receipt_path(root: Path) -> Path:
    return root / OFFLINE_SYNC_RECEIPT_NAME


def load_offline_sync_receipt(root: Path) -> dict[str, object] | None:
    """Load the latest local sync evidence without failing an operator preview."""
    path = offline_sync_receipt_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_offline_sync_receipt(plan: OfflineClonePlan) -> Path:
    path = offline_sync_receipt_path(plan.root)
    payload = {
        "receipt_version": "offline-repo-sync-v1",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "plan_generated_at": plan.generated_at,
        "clone_root": str(plan.root),
        "repositories": [
            {
                "key": entry.key,
                "display_name": entry.display_name,
                "commit": entry.commit,
                "destination": str(entry.destination_path),
                "source_dirty": entry.source_dirty,
                "action": entry.action,
                "synced": entry.executed,
                "error": entry.error,
            }
            for entry in plan.entries
        ],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    restrict_artifact_file(path)
    return path


def _destination_is_dirty(path: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=path, check=True, capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def _head(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def build_offline_clone_plan(statuses: list[RepoStatus], *, root: Path | None = None) -> OfflineClonePlan:
    """Plan clone/update actions without modifying source or operator storage."""
    target_root = root or offline_clone_root()
    entries: list[OfflineCloneEntry] = []
    for status in statuses:
        if not status.migration_scope:
            continue
        destination = target_root / status.key
        action = "clone"
        error = status.error
        if error or not status.exists or not status.git_repo or status.commit == "unknown":
            action = "blocked"
            error = error or "source repository is unavailable"
        elif destination.exists():
            if not (destination / ".git").is_dir():
                action, error = "blocked", "destination exists but is not a managed Git checkout"
            else:
                try:
                    if _destination_is_dirty(destination):
                        action, error = "blocked", "offline copy has local changes; resolve them before sync"
                    elif _head(destination) == status.commit:
                        action = "current"
                    else:
                        action = "update"
                except subprocess.CalledProcessError as exc:
                    action, error = "blocked", exc.stderr.strip() or "cannot inspect offline copy"
        entries.append(
            OfflineCloneEntry(
                key=status.key,
                display_name=status.display_name,
                source_path=status.path,
                destination_path=destination,
                commit=status.commit,
                source_dirty=status.dirty,
                action=action,
                error=error,
            )
        )
    return OfflineClonePlan(
        root=target_root,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        entries=entries,
    )


def execute_offline_clone_plan(plan: OfflineClonePlan) -> OfflineClonePlan:
    """Create or update clean independent clones; never changes source checkouts."""
    assert_operator_storage_path(plan.root)
    ensure_private_directory(plan.root)
    for entry in plan.entries:
        if entry.action in {"current", "blocked"}:
            continue
        try:
            if entry.action == "clone":
                subprocess.run(
                    ["git", "clone", "--no-hardlinks", "--origin", "mercury-source", str(entry.source_path), str(entry.destination_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            else:
                subprocess.run(
                    ["git", "fetch", "mercury-source", "--prune"], cwd=entry.destination_path,
                    check=True, capture_output=True, text=True,
                )
            subprocess.run(
                ["git", "checkout", "--detach", entry.commit], cwd=entry.destination_path,
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "fsck", "--no-dangling"], cwd=entry.destination_path,
                check=True, capture_output=True, text=True,
            )
            if _head(entry.destination_path) != entry.commit:
                raise ValueError("offline copy commit verification failed")
            entry.executed = True
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            entry.action = "blocked"
            entry.error = getattr(exc, "stderr", "") or str(exc)
    plan.receipt_path = _write_offline_sync_receipt(plan)
    return plan
