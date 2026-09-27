"""Main menu dashboard — operator-focused status snapshot."""

from __future__ import annotations

from mercury.core.environment_status import (
    backup_target_dashboard_label,
    backup_root_unsafe_reason,
    build_environment_status,
    config_dashboard_label,
    mariadb_dashboard_label,
)
from mercury.core.execution_policy import backup_root_state_is_ready
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
                dashboard_row("Dashboard", f"unavailable: {exc}"),
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

    backup_line = "skipped until config initialized"
    sync_line = "skipped until config initialized"

    rows.extend(
        [
            dashboard_row("Database backups", backup_line),
            dashboard_row("Sync readiness", sync_line),
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
    if env.primary_setup_blocker:
        rows.append(dashboard_row("Setup", env.primary_setup_blocker))
    elif env.setup_hints:
        rows.append(dashboard_row("Setup", env.setup_hints[0]))
        for hint in env.setup_hints[1:]:
            rows.append(dashboard_row("", hint))
    return rows


def _migration_dashboard_rows(report, policy) -> list[str]:
    """Compact operator home dashboard — few dense rows, no filler status."""
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
                dashboard_row("Backup storage", hdd_label),
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
                dashboard_row("Backup storage", hdd_line),
                dashboard_row("Host role", "Destination rehearsal"),
                dashboard_row("Last backup", last_backup),
                dashboard_row("Repositories", git_recovery),
                dashboard_row("Migration", migration_display),
                dashboard_row("Recommended", next_line),
            ]

        rows = [
            dashboard_row("Backup storage", hdd_line),
            dashboard_row("Last backup", last_backup),
            dashboard_row("Repositories", git_recovery),
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
            dashboard_row("Backup storage", _compact_hdd_line()),
            dashboard_row("Last backup", _compact_backup_and_git_lines()[0]),
            dashboard_row("Recommended", "Open Backup storage"),
        ]


def _compact_backup_and_git_lines() -> tuple[str, str]:
    """Lightweight Last backup / repository-backup labels."""
    last_backup = "No recent verified backup"
    git_recovery = "No recent repository backup"
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
            last_backup = f"{verified} of 4 production sources verified"
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
