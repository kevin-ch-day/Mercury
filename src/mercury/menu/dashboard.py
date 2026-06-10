"""Main menu dashboard — operator-focused status snapshot."""

from __future__ import annotations

from mercury.core.environment_status import (
    backup_target_dashboard_label,
    build_environment_status,
    config_dashboard_label,
    mariadb_dashboard_label,
    recommended_next_step,
    resolve_dashboard_blocker,
    storage_status_dashboard_label,
)
from mercury.core.execution_policy import backup_mode_label, destructive_ops_label, load_execution_policy
from mercury.core.platform import detect_platform
from mercury.core.runtime import should_probe_database_status
from mercury.terminal.theme import dashboard_row


def backup_mode_dashboard_label(policy) -> str:
    """Main menu backup safety line."""
    base = backup_mode_label(policy)
    if policy.backup_execution_allowed():
        return f"{base}; sync/deploy require confirmation"
    return base


def dashboard_rows(*, probe_database: bool | None = None) -> list[str]:
    """Sectioned operator status for the Mercury home screen."""
    probe = should_probe_database_status() if probe_database is None else probe_database
    env = build_environment_status(probe_database=probe)
    policy = env.policy
    config_initialized = env.config.initialized

    rows: list[str] = [
        dashboard_row("MariaDB", mariadb_dashboard_label(env.mariadb)),
        dashboard_row("Backup mode", backup_mode_dashboard_label(policy)),
        dashboard_row("Config", config_dashboard_label(env.config)),
        dashboard_row("Backup target", backup_target_dashboard_label(policy, env.usb)),
    ]
    platform_info = detect_platform()
    if not platform_info.is_fedora:
        rows.append(dashboard_row("Platform", platform_info.support_label))

    if policy.backup_root_state() != "usb-mounted" or env.permission_checks:
        rows.append(
            dashboard_row(
                "Storage status",
                storage_status_dashboard_label(
                    policy,
                    config=env.config,
                    usb=env.usb,
                    permission_checks=env.permission_checks,
                ),
            )
        )

    deploy_status_line = "skipped until config initialized"
    sync_blocker = "None."
    stale_names: set[str] = set()
    unknown_names: set[str] = set()
    verified_names: set[str] = set()
    source_names: set[str] = set()
    ready = 0
    blocked = 0
    deploy_complete = False
    if not config_initialized:
        source_line = "skipped until config initialized"
        sync_line = "skipped until config initialized"
        deploy_line = "skipped until config initialized"
        blocker = resolve_dashboard_blocker(
            setup_blocker=env.primary_setup_blocker,
            verified_names=set(),
            source_names=set(),
            sync_blocker="No verified full backups exist yet.",
            config_initialized=False,
        )
    else:
        verified_names, source_names, stale_names, unknown_names = _verified_source_summary(
            live=probe and env.mariadb.connection_works is True
        )
        ready, blocked, sync_blocker = _sync_readiness_summary(
            live=probe and env.mariadb.connection_works is True,
            verified_names=verified_names,
            source_names=source_names,
        )
        deploy_line = _deploy_target_summary(live=probe and env.mariadb.connection_works is True)
        deploy_status_line = _deploy_status_line(live=probe and env.mariadb.connection_works is True)
        deploy_complete = "deploy not needed" in deploy_line.lower()
        blocker = resolve_dashboard_blocker(
            setup_blocker=env.primary_setup_blocker,
            verified_names=verified_names,
            source_names=source_names,
            sync_blocker=sync_blocker,
            config_initialized=True,
            deploy_complete=deploy_complete,
        )
        source_line = f"{len(verified_names)} of {len(source_names)} verified"
        sync_line = f"{ready} ready, {blocked} need dev targets"

    source_summary = source_line
    if config_initialized:
        source_summary = f"{len(verified_names)} of {len(source_names)} artifact-verified"
        if stale_names:
            source_summary += f"; {len(stale_names)} stale"
        if unknown_names:
            source_summary += f"; {len(unknown_names)} freshness unknown"

    rows.extend(
        [
            dashboard_row("USB backups", source_summary),
            dashboard_row("MariaDB targets", deploy_line if config_initialized else "skipped until config initialized"),
            dashboard_row("Deploy status", deploy_status_line if config_initialized else "skipped until config initialized"),
            dashboard_row("Sync pairs", sync_line),
            dashboard_row("Environment", blocker),
        ]
    )
    if config_initialized and deploy_complete and sync_blocker not in {"None.", ""}:
        from mercury.deploy.rebuild_status import sync_blocker_is_rebuild_blocker

        if not sync_blocker_is_rebuild_blocker(sync_blocker, deploy_complete=True):
            rows.append(dashboard_row("Sync blocker", sync_blocker))
    if env.has_repairable_blockers:
        rows.append(dashboard_row("Repair", "Run ./run.sh doctor --repair-plan"))
    elif env.setup_hints:
        rows.append(dashboard_row("Setup", env.setup_hints[0]))
        for hint in env.setup_hints[1:]:
            rows.append(dashboard_row("", hint))
    return rows


def setup_hint_lines(*, probe_database: bool | None = None) -> list[str]:
    probe = should_probe_database_status() if probe_database is None else probe_database
    env = build_environment_status(probe_database=probe)
    return list(env.setup_hints)


def _verified_source_summary(*, live: bool) -> tuple[set[str], set[str], set[str], set[str]]:
    try:
        from mercury.backup.batch_runner import resolve_batch_sources
        from mercury.backup.status import build_backup_status_report

        report = build_backup_status_report(live=live)
        sources = set(resolve_batch_sources(live=live))
        verified = {
            entry.database
            for entry in report.entries
            if entry.protection_status == "verified"
        }
        stale = {entry.database for entry in report.entries if entry.freshness == "stale"}
        unknown = {entry.database for entry in report.entries if entry.freshness == "unknown"}
        return verified, sources, stale, unknown
    except Exception:
        return set(), set(), set(), set()


def _deploy_status_line(*, live: bool) -> str:
    if not live:
        return "skipped until MariaDB probed"
    try:
        from mercury.deploy.rebuild_status import build_rebuild_status_report

        report = build_rebuild_status_report(probe_database=True)
        return report.deploy_status
    except Exception:
        return "unknown"


def _deploy_target_summary(*, live: bool) -> str:
    if not live:
        return "skipped until MariaDB probed"
    try:
        from mercury.deploy.snapshot import (
            build_deployment_snapshot,
            deployment_target_dashboard_label,
        )

        snapshot = build_deployment_snapshot(execute=False)
        return deployment_target_dashboard_label(snapshot)
    except Exception:
        return "deploy status unavailable"


def _sync_readiness_summary(
    *,
    live: bool,
    verified_names: set[str],
    source_names: set[str],
) -> tuple[int, int, str]:
    try:
        from mercury.sync.readiness import build_sync_readiness_report

        report = build_sync_readiness_report(live=live)
        blocker = "None."
        blocker_messages: list[str] = []
        for entry in report.entries:
            blocker_messages.extend(entry.blockers)
        if blocker_messages:
            if any("freshness is stale" in msg for msg in blocker_messages):
                blocker = "Artifact-verified backups are stale; run full backup before sync."
            elif any("freshness is unknown" in msg for msg in blocker_messages):
                blocker = "Backup freshness unknown; run full backup before sync."
            elif any("No on-disk backup found for production source." in msg for msg in blocker_messages):
                missing_sources = source_names - verified_names
                sync_source_names = {entry.prod for entry in report.entries}
                if not verified_names:
                    blocker = "No verified full backups exist yet."
                elif missing_sources and missing_sources.issubset(sync_source_names):
                    blocker = "Verified backups missing for production sync sources."
                elif missing_sources:
                    blocker = "Verified backups still missing for source databases."
                else:
                    blocker = "Verified backups missing for production sync sources."
            else:
                blocker = blocker_messages[0]
        return report.ready_count, report.blocked_count, blocker
    except Exception:
        return 0, 0, "Readiness status unavailable."
