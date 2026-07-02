"""Main menu dashboard — operator-focused status snapshot."""

from __future__ import annotations

from mercury.core.environment_status import (
    backup_target_dashboard_label,
    backup_root_unsafe_reason,
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
from mercury.core.storage_status import backup_root_free_space_label
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

    rows: list[str] = []
    if env.config.initialized and env.mariadb.connection_works is True:
        rows.append(dashboard_row("Backup target", _backup_target_summary(policy, env)))
    else:
        rows.append(dashboard_row("MariaDB", mariadb_dashboard_label(env.mariadb)))
        rows.append(dashboard_row("Config", config_dashboard_label(env.config)))
        rows.append(dashboard_row("Backup target", _backup_target_summary(policy, env)))
    rows.append(dashboard_row("Backup mode", backup_mode_dashboard_label(policy)))
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
    missing_count = 0
    failed_count = 0
    unknown_only_count = 0
    ready = 0
    blocked = 0
    deploy_complete = False
    if not config_initialized:
        mariadb_line = "skipped until config initialized"
        backup_line = "skipped until config initialized"
        sync_line = "skipped until config initialized"
        deploy_line = "skipped until config initialized"
        handoff_line = "skipped until config initialized"
        blocker = resolve_dashboard_blocker(
            setup_blocker=env.primary_setup_blocker,
            verified_names=set(),
            source_names=set(),
            sync_blocker="No verified full backups exist yet.",
            config_initialized=False,
        )
    else:
        (
            verified_names,
            source_names,
            stale_names,
            unknown_names,
            missing_count,
            failed_count,
            unknown_only_count,
        ) = _verified_source_summary(live=probe and env.mariadb.connection_works is True)
        ready, blocked, sync_blocker = _sync_readiness_summary(
            live=probe and env.mariadb.connection_works is True,
            verified_names=verified_names,
            source_names=source_names,
        )
        deploy_line = _deploy_target_summary(live=probe and env.mariadb.connection_works is True)
        deploy_status_line = _deploy_status_line(live=probe and env.mariadb.connection_works is True)
        deploy_complete = "deploy not needed" in deploy_line.lower()
        blocker = _resolve_environment_readiness(
            setup_blocker=env.primary_setup_blocker,
            config_initialized=True,
            verified_names=verified_names,
            source_names=source_names,
            sync_blocker=sync_blocker,
            deploy_complete=deploy_complete,
            stale_count=len(stale_names),
            missing_count=missing_count,
            failed_count=failed_count,
            unknown_only_count=unknown_only_count,
        )
        present_count = max(0, len(source_names) - missing_count)
        backup_line = f"{len(verified_names)} of {len(source_names)} verified on USB"
        if missing_count:
            backup_line += f"; {missing_count} without backup"
        if failed_count:
            backup_line += f"; {failed_count} failed"
        if stale_names:
            backup_line += f"; {len(stale_names)} stale"
        if unknown_names:
            backup_line += f"; {len(unknown_names)} unknown freshness"
        mariadb_line = deploy_line
        sync_line = f"{ready} approved pairs ready"
        if blocked:
            sync_line += f"; {blocked} blocked"
        if stale_names or missing_count or failed_count:
            handoff_line = "partial — menu [9] or h for checklist"
        elif unknown_names:
            handoff_line = "warnings — menu [9] guided wizard"
        elif verified_names and len(verified_names) == len(source_names):
            handoff_line = "backup lane ok — menu [9] handoff wizard"
        else:
            handoff_line = "incomplete"
        latest_handoff_status: str | None = None
        latest_transfer_at: str | None = None
        try:
            from mercury.state.summary import build_state_summary

            state = build_state_summary()
            latest_transfer_at = state.latest_transfer_at
            latest_handoff_status = state.latest_handoff_status
        except OSError:
            state = None
        from mercury.handoff.display import handoff_dashboard_line

        handoff_line = handoff_dashboard_line(
            verified_count=len(verified_names),
            source_count=len(source_names),
            stale_count=len(stale_names),
            missing_count=missing_count,
            failed_count=failed_count,
            unknown_count=len(unknown_names),
            latest_handoff_status=latest_handoff_status,
            latest_transfer_at=latest_transfer_at,
        )

    rows.extend(
        [
            dashboard_row("MariaDB sources", mariadb_line),
            dashboard_row("USB backups", backup_line),
            dashboard_row("Handoff readiness", handoff_line),
            dashboard_row("Sync readiness", sync_line),
            dashboard_row("Protection", blocker),
        ]
    )
    if config_initialized and deploy_complete and sync_blocker not in {"None.", ""}:
        from mercury.deploy.rebuild_status import sync_blocker_is_rebuild_blocker

        if not sync_blocker_is_rebuild_blocker(sync_blocker, deploy_complete=True):
            rows.append(dashboard_row("Sync blocker", sync_blocker))
    if env.repairable_blockers or env.usb.repair_banner:
        from mercury.repair.usb import USB_REPAIR_COMMAND

        rows.append(
            dashboard_row(
                "USB repair",
                f"Enter r at main menu or run {USB_REPAIR_COMMAND}",
            )
        )
    elif env.setup_hints:
        rows.append(dashboard_row("Setup", env.setup_hints[0]))
        for hint in env.setup_hints[1:]:
            rows.append(dashboard_row("", hint))
    return rows


def _backup_target_summary(policy, env) -> str:
    base = backup_target_dashboard_label(policy, env.usb)
    if policy.backup_root_state() == "usb-mounted":
        free_space = backup_root_free_space_label(policy)
        if free_space:
            return f"{base} · {free_space} free"
    if env.config.initialized:
        reason = backup_root_unsafe_reason(policy, config=env.config, usb=env.usb)
        if reason and "unsafe" not in base.lower() and reason not in base:
            return f"{base} · {reason}"
    return base


def setup_hint_lines(*, probe_database: bool | None = None) -> list[str]:
    probe = should_probe_database_status() if probe_database is None else probe_database
    env = build_environment_status(probe_database=probe)
    return list(env.setup_hints)


def _resolve_environment_readiness(
    *,
    setup_blocker: str | None,
    config_initialized: bool,
    verified_names: set[str],
    source_names: set[str],
    sync_blocker: str,
    deploy_complete: bool,
    stale_count: int,
    missing_count: int,
    failed_count: int,
    unknown_only_count: int,
) -> str:
    base = resolve_dashboard_blocker(
        setup_blocker=setup_blocker,
        verified_names=verified_names,
        source_names=source_names,
        sync_blocker=sync_blocker,
        config_initialized=config_initialized,
        deploy_complete=deploy_complete,
    )
    if setup_blocker or not config_initialized:
        return base

    protection_parts: list[str] = []
    if stale_count:
        protection_parts.append(_pluralized(stale_count, "stale backup"))
    if missing_count:
        protection_parts.append(_pluralized(missing_count, "protected source missing"))
    if failed_count:
        protection_parts.append(_pluralized(failed_count, "backup verification failure"))
    if unknown_only_count:
        protection_parts.append(_pluralized(unknown_only_count, "unknown freshness state"))
    if protection_parts:
        return f"Protection incomplete: {'; '.join(protection_parts)}"
    return base


def _pluralized(count: int, singular: str) -> str:
    if count == 1:
        return f"1 {singular}"
    return f"{count} {singular}s"


def _verified_source_summary(
    *,
    live: bool,
) -> tuple[set[str], set[str], set[str], set[str], int, int, int]:
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
        unknown_only_count = max(0, report.unknown_freshness_count - report.missing_count)
        return (
            verified,
            sources,
            stale,
            unknown,
            report.missing_count,
            report.failed_count,
            unknown_only_count,
        )
    except Exception:
        return set(), set(), set(), set(), 0, 0, 0


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
