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
)
from mercury.core.execution_policy import (
    backup_root_state_is_ready,
    destructive_ops_label,
    load_execution_policy,
)
from mercury.core.platform import detect_platform
from mercury.core.runtime import should_probe_database_status
from mercury.core.storage_status import backup_root_free_space_label
from mercury.terminal.theme import dashboard_row


def dashboard_rows(*, probe_database: bool | None = None) -> list[str]:
    """Sectioned operator status for the Mercury home screen."""
    probe = should_probe_database_status() if probe_database is None else probe_database
    env = build_environment_status(probe_database=probe)
    policy = env.policy
    config_initialized = env.config.initialized

    if config_initialized:
        try:
            from mercury.migration.readiness import build_migration_readiness

            return _migration_dashboard_rows(
                build_migration_readiness(
                    probe_database=False, detailed=False
                ),
                policy,
            )
        except Exception as exc:
            return [
                dashboard_row("Active writer", _backup_target_summary(policy, env)),
                dashboard_row("Migration status", f"unavailable: {exc}"),
            ]

    rows: list[str] = []
    if env.config.initialized and env.mariadb.connection_works is True:
        rows.append(dashboard_row("Active writer", _backup_target_summary(policy, env)))
    else:
        rows.append(dashboard_row("MariaDB", mariadb_dashboard_label(env.mariadb)))
        rows.append(dashboard_row("Config", config_dashboard_label(env.config)))
        rows.append(dashboard_row("Backup target", _backup_target_summary(policy, env)))
    platform_info = detect_platform()
    if not platform_info.is_fedora:
        rows.append(dashboard_row("Platform", platform_info.support_label))

    try:
        from mercury.storage.report import build_storage_status_report

        storage_report = build_storage_status_report()
        rows.append(dashboard_row("Storage mirror", storage_report.dashboard_line()))
    except OSError:
        pass

    sync_blocker = "None."
    stale_names: set[str] = set()
    unknown_names: set[str] = set()
    verified_names: set[str] = set()
    source_names: set[str] = set()
    missing_count = 0
    failed_count = 0
    unknown_only_count = 0
    absent_count = 0
    status_error: str | None = None
    ready = 0
    blocked = 0
    if not config_initialized:
        backup_line = "skipped until config initialized"
        sync_line = "skipped until config initialized"
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
            absent_count,
            status_error,
        ) = _verified_source_summary(live=probe and env.mariadb.connection_works is True)
        ready, blocked, sync_blocker = _sync_readiness_summary(
            live=probe and env.mariadb.connection_works is True,
            verified_names=verified_names,
            source_names=source_names,
        )
        blocker = _resolve_environment_readiness(
            setup_blocker=env.primary_setup_blocker,
            config_initialized=True,
            verified_names=verified_names,
            source_names=source_names,
            sync_blocker=sync_blocker,
            # Deployment selection re-verifies every backup and is too costly
            # for a menu redraw. Deployment screens own that detailed check.
            deploy_complete=False,
            stale_count=len(stale_names),
            missing_count=missing_count,
            failed_count=failed_count,
            unknown_only_count=unknown_only_count,
            absent_count=absent_count,
            status_error=status_error,
        )
        present_on_server = max(0, len(source_names) - absent_count)
        backup_line = f"{len(verified_names)} of {present_on_server} server sources verified"
        if absent_count:
            backup_line += f"; {absent_count} absent from server"
        if missing_count:
            backup_line += f"; {missing_count} without backup"
        if failed_count:
            backup_line += f"; {failed_count} failed"
        if stale_names:
            backup_line += f"; {len(stale_names)} stale"
        if unknown_names:
            backup_line += f"; {len(unknown_names)} unknown freshness"
        if status_error:
            backup_line = status_error
        sync_line = f"{ready} approved pairs ready"
        if blocked:
            sync_line += f"; {blocked} blocked"
        if status_error:
            sync_line = "unavailable"
            handoff_line = "status error — see Protection"
        elif stale_names or missing_count or failed_count:
            handoff_line = "partial — menu [10] or h for checklist"
        elif unknown_names or absent_count:
            handoff_line = "warnings — menu [10] guided wizard"
        elif verified_names and present_on_server and len(verified_names) == present_on_server:
            handoff_line = "backup lane ok — menu [10] handoff wizard"
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
            absent_count=absent_count,
            latest_handoff_status=latest_handoff_status,
            latest_transfer_at=latest_transfer_at,
        )

    rows.extend(
        [
            dashboard_row("Database backups", backup_line),
            dashboard_row("Workstation handoff", handoff_line),
            dashboard_row("Sync readiness", sync_line),
            dashboard_row("Cutover blockers", blocker),
        ]
    )
    if env.repairable_blockers or env.usb.repair_banner:
        from mercury.core.environment_status import _hdd_writer_active

        if _hdd_writer_active():
            if env.repairable_blockers:
                rows.append(
                    dashboard_row(
                        "Storage repair",
                        "Run ./run.sh storage validate (HDD is the active writer)",
                    )
                )
        else:
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


def _migration_dashboard_rows(report, policy) -> list[str]:
    """Compact dashboard backed by one shared migration-evidence report."""
    by_id = {check.id: check for check in report.checks}
    active = by_id["active_writer"]
    mirror = by_id["storage_mirror"]
    duplicate = by_id["duplicate_primary_mount"]
    backups = by_id["database_backups"]
    unresolved = report.unresolved_checks
    free = backup_root_free_space_label(policy)
    active_text = active.summary
    try:
        from mercury.storage.host_maintenance import load_host_maintenance

        host = load_host_maintenance()
        if host.storage_availability in {"detaching", "detached"} or not host.writes_allowed:
            active_text = (
                f"HDD {host.storage_availability} · writes disabled · "
                "destination rehearsal (cutover NOT complete)"
            )
            free = None
    except OSError:
        pass
    if free:
        active_text += f" · {free} free"
    mirror_text = "Verified" if mirror.state.value == "PASS" else mirror.summary
    if duplicate.state.value == "WARNING":
        mirror_text += " · HDD mounted twice"
    package_text = "Review required" if report.overall_status.value != "PASS" else "Ready"
    # Dashboard mode deliberately avoids scanning large worktrees or runtime
    # configuration.  Do not turn the absence of that expensive scan into a
    # claim that a capture is incomplete.
    if any(
        check.id in {"erebus_web_worktree", "scytaledroid_web_worktree"}
        and check.unresolved
        for check in report.checks
    ):
        package_text += " · capture status not rechecked"
    next_open = next((check for check in unresolved if check.id == "destination_validation"), None)
    blocker_text = f"{len(unresolved)} open"
    if next_open is not None:
        blocker_text += " · destination not validated"
    return [
        dashboard_row("Active writer", active_text),
        dashboard_row("Storage mirror", mirror_text),
        dashboard_row("Database backups", backups.summary),
        dashboard_row("Migration package", package_text),
        dashboard_row("Destination package", _destination_package_dashboard_line()),
        dashboard_row("Cleanup", _cleanup_dashboard_line()),
        dashboard_row("Migration phase", report.operator_phase.title()),
        dashboard_row("Cutover blockers", blocker_text),
    ]


def _destination_package_dashboard_line() -> str:
    from mercury.storage.host_maintenance import load_host_maintenance
    from mercury.storage.retention import load_retention_policy

    host = load_host_maintenance()
    if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED" and host.package_id:
        return f"VERIFIED · {host.package_id}"
    policy = load_retention_policy()
    mercury_pending = not policy.current_destination_mercury_commit
    size_note = "allowlist only · Scytale excluded"
    if mercury_pending:
        return (
            "Explicit allowlist required · ScytaleDroid excluded by default · "
            "Phase 3B pinned · Mercury committed capture pending · "
            f"Estimated transfer size: {size_note}"
        )
    return (
        "Explicit allowlist required · ScytaleDroid excluded by default · "
        "Phase 3B pinned · "
        f"Mercury capture {policy.current_destination_mercury_capture_id or policy.current_destination_mercury_commit[:12]} · "
        f"Estimated transfer size: {size_note}"
    )


def _cleanup_dashboard_line() -> str:
    from mercury.storage.retention import load_retention_policy

    policy = load_retention_policy()
    return (
        "Preview available · Execution locked until destination validation · "
        f"Safe candidate estimate: {policy.safe_candidate_estimate_gib:.1f} GiB · "
        f"Manual-review project data: ~{policy.manual_review_project_estimate_gib:.0f} GiB"
    )


def _backup_target_summary(policy, env) -> str:
    base = backup_target_dashboard_label(policy, env.usb)
    if backup_root_state_is_ready(policy.backup_root_state()):
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
    absent_count: int = 0,
    status_error: str | None = None,
) -> str:
    if status_error:
        return status_error
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
        protection_parts.append(_pluralized(missing_count, "protected source missing backup"))
    if failed_count:
        protection_parts.append(_pluralized(failed_count, "backup verification failure"))
    if unknown_only_count:
        protection_parts.append(_pluralized(unknown_only_count, "unknown freshness state"))
    if absent_count:
        protection_parts.append(
            _pluralized(absent_count, "catalog source absent from this server")
        )
    if protection_parts:
        # Absent-only is a warning, not incomplete protection for this host.
        if absent_count and not (stale_count or missing_count or failed_count or unknown_only_count):
            return f"Host note: {'; '.join(protection_parts)}"
        if absent_count and not (stale_count or missing_count or failed_count):
            return f"Protection warnings: {'; '.join(protection_parts)}"
        return f"Protection incomplete: {'; '.join(protection_parts)}"
    return base


def _pluralized(count: int, singular: str) -> str:
    if count == 1:
        return f"1 {singular}"
    return f"{count} {singular}s"


def _verified_source_summary(
    *,
    live: bool,
) -> tuple[set[str], set[str], set[str], set[str], int, int, int, int, str | None]:
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
        unknown_only_count = max(
            0,
            report.unknown_freshness_count - report.missing_count,
        )
        return (
            verified,
            sources,
            stale,
            unknown,
            report.missing_count,
            report.failed_count,
            unknown_only_count,
            report.absent_count,
            None,
        )
    except Exception as exc:
        return set(), set(), set(), set(), 0, 0, 0, 0, f"Backup status unavailable: {exc}"


def _deploy_status_line(*, live: bool) -> str:
    if not live:
        return "skipped until MariaDB probed"
    try:
        from mercury.deploy.rebuild_status import build_rebuild_status_report

        report = build_rebuild_status_report(probe_database=True)
        return report.deploy_status
    except Exception as exc:
        return f"deploy status unavailable: {exc}"


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
    except Exception as exc:
        return f"deploy status unavailable: {exc}"


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
