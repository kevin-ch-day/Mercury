"""Read-only operational protection observations, with historical assurance labels."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.core.storage_roots import load_storage_config
from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES
from mercury.repo.config import load_repo_definitions
from mercury.storage.verified_volume import require_disk


def read_json(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            return {}
        x = json.loads(path.read_text())
        return x if isinstance(x, dict) else {}
    except (OSError, ValueError):
        return {}


def _unit_state() -> dict:
    try:
        p = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "mercury-repo-sync.service",
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "Result",
                "-p",
                "ExecMainStatus",
                "-p",
                "ExecMainStartTimestamp",
                "-p",
                "ExecMainExitTimestamp",
                "-p",
                "MainPID",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
    except (OSError, subprocess.SubprocessError):
        return {"observation": "unavailable"}


def _lock_active(path: Path) -> bool | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def build_health() -> dict:
    root = load_storage_config().active_write_root
    mount = root.mount_path
    out = {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "mount": str(mount),
        "expected_uuid": root.filesystem_uuid,
        "service": _unit_state(),
        "warnings": [],
        "assurance": "Live mount/service observation; backup and recovery receipts are historical. No payload hashing or application execution.",
    }
    try:
        require_disk(str(mount), root.filesystem_uuid)
    except (OSError, ValueError) as exc:
        out["physical_mount"] = "unavailable_or_invalid"
        out["warnings"].append(str(exc))
        return out  # Do not trigger an automount to render read-only status.
    out["physical_mount"] = "verified_ext4_writable_uuid"
    out["available_bytes"] = shutil.disk_usage(mount).free
    out["active_database_backup"] = _lock_active(
        mount / ".mercury-database-backup.lock"
    )
    out["active_repository_backup"] = _lock_active(mount / ".mercury-repo-sync.lock")
    receipt = read_json(mount / "mercury_repo_clones/offline_repo_sync_receipt.json")
    out["latest_successful_repository_receipt"] = receipt.get("recorded_at_utc")
    out["repository_receipt_status"] = receipt.get("status", "legacy_receipt")
    definitions = {str(x.path): x for x in load_repo_definitions()}
    entries = {x["key"]: x for x in receipt.get("repositories", [])}
    components = [
        "android-device",
        "android-static",
        "android-dynamic",
        "android-web",
        "noctivar-cli-database",
        "noctivar-cli-utls",
    ]
    coverage = {}
    for component in components:
        path = str(Path.home() / "Projects/Noctivar" / component)
        definition = definitions.get(path)
        entry = entries.get(definition.key, {}) if definition else {}
        snapshot = entry.get("snapshot") or {}
        coverage[component] = {
            "configured": definition is not None,
            "recorded_synced": entry.get("synced", False),
            "commit": entry.get("commit"),
            "recorded_source_dirty": entry.get("source_dirty"),
            "snapshot_at": entry.get("snapshot_at"),
            "snapshot_saved_files": snapshot.get("saved_files"),
            "snapshot_excluded_files": snapshot.get("excluded_count"),
            "snapshot_manifest_pin": snapshot.get("manifest_sha256"),
        }
        if not definition or not entry.get("synced"):
            out["warnings"].append(
                f"{component}: missing configured or successful repository coverage"
            )
    out["repositories"] = coverage
    databases = {}
    for name in sorted(ACTIVE_BACKUP_SOURCE_DATABASES):
        p = find_latest_backup_directory(mount / "mercury_backups", name)
        manifest = read_json(p / "manifest.json") if p else {}
        created = manifest.get("created_at")
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(created)).total_seconds()
        except (ValueError, TypeError, AttributeError):
            age = None
        databases[name] = {
            "directory": str(p) if p else None,
            "backup_id": manifest.get("backup_id"),
            "recorded_verified": manifest.get("verified", False),
            "created_at": created,
            "age_seconds": age,
            "stale_after_days": 2,
            "stale": age is None or age > 2 * 86400,
        }
        if not manifest or age is None or age > 2 * 86400:
            out["warnings"].append(f"{name}: missing or stale full database backup")
    out["databases"] = databases
    incomplete = []
    for p in sorted((mount / "mercury_backup_incomplete").glob("*/STATE.json"))[-200:]:
        state = read_json(p)
        if state.get("status") != "complete":
            incomplete.append({"path": str(p), **state})
    out["incomplete_database_generations"] = incomplete
    out["research_protection"] = read_json(
        mount / "mercury_research_backups/noctivar/PROTECTION.json"
    )
    rehearsals = sorted(
        (mount / "mercury_restore_checks/noctivar-phase1").glob("*/REHEARSAL.json")
    )
    out["latest_recovery_rehearsal"] = read_json(rehearsals[-1]) if rehearsals else None
    runs = sorted((mount / "mercury_repo_clones/generations").glob("*/STATE.json"))
    out["latest_failed_or_partial_repository_generations"] = [
        {"path": str(p), **read_json(p)}
        for p in runs[-30:]
        if read_json(p).get("status") in {"failed", "partial", "incomplete"}
    ]
    if not out["research_protection"]:
        out["warnings"].append("Research evidence protection registry is unavailable")
    return out
