"""Read-only operational protection observations, with historical assurance labels."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.backup.freshness import parse_backup_timestamp
from mercury.backup.layout import MANIFEST_FILENAME
from mercury.backup.status import latest_restore_check_by_backup_id
from mercury.backup.verification import manifest_verified_stamp
from mercury.core.safety import LIVE_RESTORE_PROHIBITED_DATABASES
from mercury.core.storage_roots import load_storage_config
from mercury.database.core.scope import ACTIVE_BACKUP_SOURCE_DATABASES
from mercury.repo.config import load_repo_definitions
from mercury.repo.offline_clone import OFFLINE_CLONE_DIRNAME, offline_sync_receipt_path
from mercury.storage.verified_volume import require_disk


def read_json(path: Path) -> dict:
    """Read one bounded snapshot of a regular metadata file."""
    maximum_size = 2 * 1024 * 1024

    def open_without_following(name: str, flags: int) -> int:
        return os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK)

    try:
        with open(path, "rb", opener=open_without_following) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum_size:
                return {}
            contents = stream.read(maximum_size + 1)
        if len(contents) > maximum_size:
            return {}
        payload = json.loads(contents)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, RecursionError):
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
        fields = dict(line.split("=", 1) for line in p.stdout.splitlines() if "=" in line)
        return fields if p.returncode == 0 and fields else {"observation": "unavailable"}
    except (OSError, subprocess.SubprocessError):
        return {"observation": "unavailable"}


def _lock_active(path: Path) -> bool | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return None
    finally:
        os.close(fd)


def _repository_receipt(receipt: dict) -> tuple[str, dict[str, dict], str | None]:
    if not receipt:
        return "unavailable", {}, None
    rows = receipt.get("repositories")
    status = receipt.get("status", "legacy_receipt")
    if not isinstance(rows, list) or status not in ("complete", "legacy_receipt", "partial", "failed", "incomplete"):
        return "invalid", {}, None
    entries = {}
    for row in rows:
        key = row.get("key") if isinstance(row, dict) else None
        if not isinstance(key, str) or not key or key in entries:
            return "invalid", {}, None
        entries[key] = row
    recorded_at = receipt.get("recorded_at_utc")
    if status in ("complete", "legacy_receipt") and (
        entries
        and all(row.get("synced") is True for row in entries.values())
        and isinstance(recorded_at, str)
        and parse_backup_timestamp(recorded_at) is not None
    ):
        return status, entries, recorded_at
    return ("invalid" if status == "complete" else status), entries, None


def _database_next_action(database: str, *, stale: bool, verified: bool, restore_status: str | None) -> str | None:
    backup_only = database in LIVE_RESTORE_PROHIBITED_DATABASES
    if stale:
        return "review_complete_backup_access" if backup_only else "refresh_full_backup"
    if not verified:
        return "verify_backup"
    if restore_status != "passed":
        if backup_only:
            return "plan_isolated_restore_rehearsal"
        if restore_status in ("failed", "verification_failed"):
            return "investigate_restore_check_failure"
        return "run_restore_check"
    return None


def build_health() -> dict:
    now = datetime.now(UTC)
    root = load_storage_config().active_write_root
    mount = root.mount_path
    out = {
        "observed_at_utc": now.isoformat(),
        "mount": str(mount),
        "expected_uuid": root.filesystem_uuid,
        "service": _unit_state(),
        "warnings": [],
        "assurance": "Live mount/service observation; backup and recovery receipts are historical. No payload hashing or application execution.",
    }
    try:
        require_disk(str(mount), root.filesystem_uuid)
        free_bytes = shutil.disk_usage(mount).free
    except (OSError, ValueError) as exc:
        out["physical_mount"] = "unavailable_or_invalid"
        out["warnings"].append(str(exc))
        return out  # Do not trigger an automount to render read-only status.
    out["physical_mount"] = "verified_ext4_writable_uuid"
    out["available_bytes"] = free_bytes
    out["active_database_backup"] = _lock_active(
        mount / ".mercury-database-backup.lock"
    )
    out["active_repository_backup"] = _lock_active(mount / ".mercury-repo-sync.lock")
    clone_root = mount / OFFLINE_CLONE_DIRNAME
    receipt_status, entries, recorded_at = _repository_receipt(
        read_json(offline_sync_receipt_path(clone_root))
    )
    if recorded_at is None:
        out["warnings"].append("No successful repository backup receipt could be read")
    out["repository_receipt_status"] = receipt_status
    out["latest_successful_repository_receipt"] = recorded_at
    definitions = {str(x.path): x for x in load_repo_definitions()}
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
        snapshot = entry.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        synced = entry.get("synced") is True
        coverage[component] = {
            "configured": definition is not None,
            "recorded_synced": synced,
            "commit": entry.get("commit"),
            "recorded_source_dirty": entry.get("source_dirty"),
            "snapshot_at": entry.get("snapshot_at"),
            "snapshot_saved_files": snapshot.get("saved_files"),
            "snapshot_excluded_files": snapshot.get("excluded_count"),
            "snapshot_manifest_pin": snapshot.get("manifest_sha256"),
        }
        if not synced:
            out["warnings"].append(
                f"{component}: missing configured or successful repository coverage"
            )
    out["repositories"] = coverage
    databases = {}
    restore_records = latest_restore_check_by_backup_id()
    for name in sorted(ACTIVE_BACKUP_SOURCE_DATABASES):
        p = find_latest_backup_directory(
            mount / "mercury_backups", name, require_complete_full=True
        )
        manifest_path = p / MANIFEST_FILENAME if p else None
        manifest = read_json(manifest_path) if manifest_path else {}
        created = manifest.get("created_at")
        recorded_verified = bool(manifest_path and manifest_verified_stamp(manifest_path) is True)
        created_at = parse_backup_timestamp(created) if isinstance(created, str) else None
        age = (now - created_at).total_seconds() if created_at is not None else None
        stale = age is None or age < 0 or age > 2 * 86400
        backup_id = manifest.get("backup_id")
        record = restore_records.get(backup_id) if isinstance(backup_id, str) else None
        if record is not None and record.database != name:
            record = None
        restore_status = record.status if record else None
        next_action = _database_next_action(
            name, stale=stale, verified=recorded_verified, restore_status=restore_status,
        )
        databases[name] = {
            "directory": str(p) if p else None,
            "backup_id": backup_id,
            "recorded_verified": recorded_verified,
            "created_at": created,
            "age_seconds": age,
            "stale_after_days": 2,
            "stale": stale,
            "recorded_restore_check_status": restore_status,
            "recorded_restore_check_at": record.timestamp if record else None,
            "next_action": next_action,
        }
        if stale:
            warning = f"{name}: missing or stale full database backup"
            if next_action == "review_complete_backup_access":
                warning += ". Review complete backup access."
            out["warnings"].append(warning)
        if manifest and not recorded_verified:
            out["warnings"].append(f"{name}: full database backup has no passing verification stamp")
        if next_action in ("run_restore_check", "investigate_restore_check_failure", "plan_isolated_restore_rehearsal"):
            action_text = next_action.replace("_", " ")
            out["warnings"].append(f"{name}: {action_text} for {backup_id}")
    out["databases"] = databases
    incomplete = []
    for p in sorted((mount / "mercury_backup_incomplete").glob("*/STATE.json"))[-200:]:
        state = read_json(p)
        if state.get("status") != "complete":
            incomplete.append({**state, "path": str(p)})
    out["incomplete_database_generations"] = incomplete
    out["research_protection"] = read_json(
        mount / "mercury_research_backups/noctivar/PROTECTION.json"
    )
    rehearsals = sorted(
        (mount / "mercury_restore_checks/noctivar-phase1").glob("*/REHEARSAL.json")
    )
    out["latest_recovery_rehearsal"] = read_json(rehearsals[-1]) if rehearsals else None
    runs = sorted((clone_root / "generations").glob("*/STATE.json"))
    out["latest_failed_or_partial_repository_generations"] = [
        {**state, "path": str(p)}
        for p in runs[-30:]
        if (state := read_json(p)).get("status") in ("failed", "partial", "incomplete")
    ]
    if not out["research_protection"]:
        out["warnings"].append("Research evidence protection registry is unavailable")
    return out
