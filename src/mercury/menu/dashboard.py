"""Main menu dashboard — operator-focused status snapshot."""

from __future__ import annotations

import socket
from pathlib import Path

from mercury.core.execution_policy import load_execution_policy
from mercury.core.paths import LOCAL_CONFIG
from mercury.core.runtime import should_probe_database_status
from mercury.terminal.format import format_bytes
from mercury.terminal.theme import body_label, dashboard_row
from mercury.core.runtime import operator_status


def dashboard_rows(*, probe_database: bool | None = None) -> list[str]:
    """Sectioned operator status for the Mercury home screen."""
    probe = should_probe_database_status() if probe_database is None else probe_database
    status = operator_status(probe_database=probe)
    connected = "connected" in status["database"].lower() and "not connected" not in status["database"].lower()
    policy = load_execution_policy()
    probe_result = _server_probe(probe and connected)

    rows: list[str] = [
        body_label("Environment"),
        dashboard_row("Host", socket.gethostname()),
        dashboard_row("MariaDB", "[ok] connected" if connected else "[!!] unavailable"),
    ]
    if probe_result is not None and probe_result.connected and probe_result.server_version:
        rows.append(dashboard_row("Server version", probe_result.server_version))
    rows.append(dashboard_row("Config", "config/local.toml" if LOCAL_CONFIG.exists() else "fallback/default"))

    rows.extend(
        [
            "",
            body_label("Execution Safety"),
            dashboard_row("Mode", "[--] DRY RUN" if policy.dry_run else "[ok] LIVE"),
            dashboard_row("Live actions", "[--] disabled" if not policy.live_actions_enabled else "[ok] enabled"),
            dashboard_row(
                "Destructive sync",
                "[--] blocked"
                if policy.dry_run or not policy.live_actions_enabled
                else "[ok] enabled for ready pairs only",
            ),
            "",
            body_label("Backup Storage"),
            dashboard_row("Target", str(policy.backup_root.resolve())),
            dashboard_row("Mount", _mount_label(policy)),
        ]
    )

    filesystem = _filesystem_label(policy)
    if filesystem is not None:
        rows.append(dashboard_row("Filesystem", filesystem))

    free_space = _free_space_label(policy)
    if free_space is not None:
        rows.append(dashboard_row("Free space", free_space))

    rows.append(dashboard_row("Storage status", _storage_status_label(policy)))

    verified_count, source_total = _verified_source_summary(live=probe and connected)
    backup_count = _count_on_disk_backups(policy.backup_root) or 0
    ready, blocked, blocker = _sync_readiness_summary(live=probe and connected)

    rows.extend(
        [
            "",
            body_label("Protection"),
            dashboard_row("Source DBs verified", f"{verified_count} of {source_total} verified"),
            dashboard_row("USB backups", str(backup_count)),
            dashboard_row("Sync pairs", f"{ready} ready, {blocked} blocked"),
            dashboard_row("Blocker", blocker),
        ]
    )
    return rows


def _verified_source_summary(*, live: bool) -> tuple[int, int]:
    try:
        from mercury.backup.batch_runner import resolve_batch_sources
        from mercury.backup.find_latest_backup import find_latest_backup_directory
        from mercury.backup.verification import verify_backup_artifacts
        from mercury.core.safety import BACKUP_KIND_FULL

        policy = load_execution_policy()
        sources = resolve_batch_sources(live=live)
        verified = 0
        for name in sources:
            backup_dir = find_latest_backup_directory(policy.backup_root, name)
            if backup_dir is None:
                continue
            if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
                continue
            result = verify_backup_artifacts(backup_dir, database=name)
            if result.verified and result.backup_kind == BACKUP_KIND_FULL:
                verified += 1
        return verified, len(sources)
    except Exception:
        return 0, 0


def _count_on_disk_backups(backup_root) -> int | None:
    try:
        from mercury.backup.on_disk_index import build_on_disk_backup_list

        policy = load_execution_policy()
        if policy.backup_root_is_within_repo() and not policy.allow_unsafe_backup_root:
            return 0
        listing = build_on_disk_backup_list(backup_root)
        return len(listing.records)
    except OSError:
        return None


def _sync_readiness_summary(*, live: bool) -> tuple[int, int, str]:
    try:
        from mercury.sync.readiness import build_sync_readiness_report

        report = build_sync_readiness_report(live=live)
        blocker = "None."
        blocker_messages: list[str] = []
        for entry in report.entries:
            blocker_messages.extend(entry.blockers)
        if blocker_messages:
            if any("No on-disk backup found for production source." in msg for msg in blocker_messages):
                blocker = "No verified full backups exist yet."
            else:
                blocker = blocker_messages[0]
        return report.ready_count, report.blocked_count, blocker
    except Exception:
        return 0, 0, "Readiness status unavailable."


def _server_probe(enabled: bool):
    if not enabled:
        return None
    try:
        from mercury.database.mariadb.session import probe_mariadb_server

        return probe_mariadb_server(include_database_sample=False)
    except Exception:
        return None


def _mount_label(policy) -> str:
    state = policy.backup_root_state()
    if state == "usb-mounted":
        return "[ok] mounted"
    if state == "repo-local fallback":
        return "[!!] repo-local fallback"
    if state == "usb not mounted":
        return "[!!] not mounted"
    if state == "unsafe path":
        return "[!!] unsafe path"
    if state == "missing path":
        return "[!!] missing path"
    if state == "low free space":
        return "[--] mounted"
    return "[--] unknown"


def _filesystem_label(policy) -> str | None:
    mount_info = _mount_info(policy.backup_root)
    if mount_info is None:
        return None
    return mount_info[1]


def _free_space_label(policy) -> str | None:
    free_bytes = policy.backup_root_free_bytes()
    if free_bytes is None:
        return None
    return format_bytes(free_bytes)


def _storage_status_label(policy) -> str:
    state = policy.backup_root_state()
    if state == "usb-mounted":
        return "[ok] ready"
    if state == "low free space":
        return "[--] warning"
    return "[!!] unsafe"


def _mount_info(path: Path) -> tuple[Path, str] | None:
    try:
        with Path("/proc/mounts").open("r", encoding="utf-8") as handle:
            mounts = []
            for line in handle:
                parts = line.split()
                if len(parts) >= 3:
                    mounts.append((Path(parts[1]), parts[2]))
    except OSError:
        return None

    resolved = path.resolve()
    matches = [(mount_path, fs_type) for mount_path, fs_type in mounts if str(resolved).startswith(str(mount_path))]
    if not matches:
        return None
    return max(matches, key=lambda item: len(str(item[0])))
