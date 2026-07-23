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
            handoff_line = "partial — handoff checklist (or h)"
        elif unknown_names or absent_count:
            handoff_line = "warnings — handoff guided wizard (or h)"
        elif verified_names and present_on_server and len(verified_names) == present_on_server:
            handoff_line = "backup lane ok — handoff wizard (or h)"
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
    """Compact handoff dashboard — few dense rows, no filler status."""
    unresolved = report.unresolved_checks
    try:
        from mercury.storage.hdd_menu_options import (
            dashboard_hdd_status_line,
            dashboard_next_action_short,
        )
        from mercury.storage.lifecycle import (
            MigrationHostRole,
            StorageLifecycleState,
            assess_storage_lifecycle,
        )

        snap = assess_storage_lifecycle(probe_disconnect=True)
        hdd_line = dashboard_hdd_status_line(snap)
        next_line = dashboard_next_action_short(snap)
        package_line = _compact_package_line(snapshot=snap)
        delta_line = _compact_source_delta_line()
        last_backup, git_recovery = _compact_backup_and_git_lines()
        migration_display = _compact_phase_line(report.operator_phase, unresolved)

        if snap.state == StorageLifecycleState.DETACHED:
            from mercury.storage.host_maintenance import (
                intentional_safe_disconnect_active,
                load_host_maintenance,
            )

            intentional = intentional_safe_disconnect_active(load_host_maintenance())
            hdd_label = (
                "Powered off · safe to unplug"
                if intentional
                else "Not connected"
            )
            rows = [
                dashboard_row("Mercury HDD", hdd_label),
                dashboard_row("Writer", "Disabled"),
                dashboard_row("Recommended", next_line),
                dashboard_row(
                    "Package",
                    package_line
                    if package_line != "Pending"
                    else ("VERIFIED · destination rehearsal" if intentional else "Located on detached HDD"),
                ),
            ]
            if snap.host_role == MigrationHostRole.SOURCE_OPERATION and not intentional:
                rows.insert(3, dashboard_row("Host role", "Source reference system"))
            return rows

        if snap.state == StorageLifecycleState.ATTACHED_READ_ONLY or (
            snap.host_role == MigrationHostRole.DESTINATION_REHEARSAL
            and snap.state
            in {
                StorageLifecycleState.ATTACHED_WRITER_DISABLED,
                StorageLifecycleState.ATTACHED_READ_ONLY,
                StorageLifecycleState.RECONNECT_VALIDATED,
            }
        ):
            return [
                dashboard_row("Mercury HDD", hdd_line),
                dashboard_row("Host role", "Destination rehearsal"),
                dashboard_row("Last backup", last_backup),
                dashboard_row("Git recovery", git_recovery),
                dashboard_row("Migration", migration_display),
                dashboard_row("Recommended", next_line),
            ]

        rows = [
            dashboard_row("Mercury HDD", hdd_line),
            dashboard_row("Last backup", last_backup),
            dashboard_row("Git recovery", git_recovery),
            dashboard_row("Migration", migration_display),
            dashboard_row("Recommended", next_line),
        ]
        if delta_line:
            rows.append(dashboard_row("Source delta", delta_line))
        return rows
    except OSError:
        by_id = {check.id: check for check in report.checks}
        free = backup_root_free_space_label(policy)
        return [
            dashboard_row("Writer", _compact_writer_line(by_id["active_writer"].summary, free)),
            dashboard_row("Mercury HDD", _compact_hdd_line()),
            dashboard_row("Package", _compact_package_line()),
            dashboard_row("Migration", _compact_phase_line(report.operator_phase, unresolved)),
            dashboard_row("Recommended", "Open Mercury HDD and Storage"),
        ]


def _compact_backup_and_git_lines() -> tuple[str, str]:
    """Lightweight Last backup / Git recovery labels (no live writer or HDD mutation)."""
    last_backup = "No recent verified backup"
    git_recovery = "No recent Git capture"
    try:
        from mercury.state.summary import build_state_summary

        state = build_state_summary()
        for attr, fmt in (
            ("latest_verified_backup_at", "Verified · {}"),
            ("latest_backup_at", "{}"),
        ):
            value = getattr(state, attr, None)
            if value:
                last_backup = fmt.format(value)
                break
        verified = getattr(state, "verified_source_count", None)
        if last_backup.startswith("No recent") and verified:
            last_backup = f"{verified} verified source(s)"
        for attr, fmt in (
            ("latest_repo_bundle_at", "Bundle · {}"),
            ("repo_bundle_rows", "{} repo bundle(s) on storage"),
        ):
            value = getattr(state, attr, None)
            if value:
                git_recovery = fmt.format(value)
                break
    except Exception:
        pass
    return last_backup, git_recovery


def _compact_writer_line(fallback: str, free: str | None) -> str:
    try:
        from mercury.storage.host_maintenance import load_host_maintenance

        host = load_host_maintenance()
        if host.storage_availability == "detached":
            return "Disabled"
        if host.storage_availability == "detaching" or not host.writes_allowed:
            return "Disabled · preparation active"
    except OSError:
        pass
    text = fallback
    if free:
        text = f"{text} · {free} free"
    return text


def _compact_hdd_line() -> str:
    try:
        from mercury.storage.lifecycle import assess_storage_lifecycle

        return assess_storage_lifecycle(probe_disconnect=True).label
    except OSError:
        return "Unknown"


def _compact_package_line(*, snapshot=None) -> str:
    from mercury.storage.host_maintenance import load_host_maintenance

    host = load_host_maintenance()
    status = snapshot.package_status if snapshot is not None else host.package_verification_status
    package_id = snapshot.package_id if snapshot is not None else host.package_id
    if status == "DESTINATION_PACKAGE_VERIFIED":
        if getattr(host, "source_data_changed_since_package", False):
            return "VERIFIED · source data changed since package"
        if getattr(host, "recovery_artifacts_created_after_package", False) or host.source_changed_since_package:
            return "VERIFIED · recovery after package"
        if host.source_writes_resumed_after_package:
            return "VERIFIED · rehearsal snapshot"
        rehearsal = False
        if snapshot is not None:
            rehearsal = snapshot.host_role.value == "DESTINATION_REHEARSAL"
        else:
            rehearsal = bool(
                host.destination_rehearsal_active or host.destination_rehearsal_in_progress
            )
        if rehearsal:
            return "VERIFIED · destination rehearsal"
        if package_id:
            pkg = package_id
            if len(pkg) > 42:
                pkg = "…" + pkg[-34:]
            return f"VERIFIED · {pkg}"
        return "VERIFIED"
    if snapshot is not None and snapshot.state.value == "DETACHED" and package_id:
        return "Verified on detached HDD"
    return "Pending"


def _compact_source_delta_line() -> str | None:
    from mercury.storage.host_maintenance import load_host_maintenance

    host = load_host_maintenance()
    if not host.source_writes_resumed_after_package:
        return None
    if getattr(host, "source_data_changed_since_package", False):
        return "Source data changed since package"
    if getattr(host, "recovery_artifacts_created_after_package", False) or host.source_changed_since_package:
        return "Recovery artifacts created after package"
    return "New writes possible after package"


def _compact_phase_line(operator_phase: str, unresolved) -> str:
    phase = operator_phase.replace("_", " ").title()
    # Prefer "pending" so the row does not read as validation in progress/complete.
    phase = phase.replace(
        "Destination Validation Pending", "Destination validation pending"
    )
    phase = phase.replace("Destination Validation", "Destination validation pending")
    blockers = len(unresolved)
    if blockers == 0:
        return phase
    return f"{phase} · {blockers} open"


def _compact_cleanup_line() -> str:
    return "Preview only · execution locked"


def _mercury_hdd_dashboard_line() -> str:
    return _compact_hdd_line()


def _safe_disconnect_dashboard_line() -> str:
    from mercury.storage.host_maintenance import load_host_maintenance

    host = load_host_maintenance()
    if host.storage_availability == "detached":
        return "Detached"
    if host.package_verification_status != "DESTINATION_PACKAGE_VERIFIED":
        return "Blocked: package"
    try:
        from mercury.storage.detach_wizard import run_detach_preflight

        pre = run_detach_preflight(skip_log_redirect=True, mutate_host=False)
    except OSError:
        return "Unknown"
    if pre.result_state == "PREFLIGHT_OK":
        return "Ready"
    if pre.result_state == "HDD_ALREADY_DETACHED":
        return "Detached"
    if pre.blockers:
        reason = pre.blockers[0]
        if len(reason) > 36:
            reason = reason[:33] + "…"
        return f"Blocked: {reason}"
    return pre.result_state


def _destination_package_dashboard_line() -> str:
    return _compact_package_line()


def _cleanup_dashboard_line() -> str:
    return _compact_cleanup_line()


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
