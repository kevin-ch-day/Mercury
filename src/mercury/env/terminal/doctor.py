"""Terminal output for mercury doctor."""

from __future__ import annotations

from mercury import output
from mercury.env.doctor import DoctorReport, build_repair_plan


def print_doctor_report(report: DoctorReport) -> None:
    output.heading("MERCURY DOCTOR")
    output.write("Repo")
    output.field("path", str(report.repo_root))
    output.field("user", report.current_user)
    output.field("python", report.python_version)
    output.field("platform", report.platform_label)
    output.write("")
    output.write("Config")
    config = report.config
    output.field("local.toml", "present" if config.local_toml_present else "missing")
    output.field("databases.toml", "present" if config.databases_toml_present else "missing")
    output.field("repos.toml", "present" if config.repos_toml_present else "missing")
    output.write("")
    output.write("Storage roles")
    storage = None
    try:
        from mercury.storage.report import build_storage_status_report, suggested_primary_fstab_line

        storage = build_storage_status_report()
        output.field("active writer", storage.active_write_role.value)
        output.field("migration state", storage.migration_state.value)
        output.field("primary", storage.primary.one_line())
        output.field("legacy", storage.legacy.one_line())
        if not storage.primary.validation.ok and not storage.primary.validation.identity.is_mount:
            output.field(
                "primary fstab draft",
                suggested_primary_fstab_line(storage.config),
            )
            output.field("primary note", "Operator action only — Mercury does not edit fstab.")
    except Exception as exc:
        output.field("storage status", f"unavailable — {exc}")
    output.write("")
    usb = report.usb
    primary_active = bool(storage and storage.active_write_role.value == "primary")
    if primary_active:
        output.write("Active operator storage")
        output.field("mount", str(storage.primary.mount_path))
        output.field(
            "status",
            "mounted and writable"
            if storage.primary.validation.ok
            else (storage.primary.validation.blocker or storage.primary.validation.code.value),
        )
        output.field("legacy archive", storage.legacy.one_line())
    else:
        output.write("Legacy / active-write mount")
        output.field("mount", str(usb.mount_path))
        output.field("mounted", "yes" if usb.mounted else "no")
        if getattr(usb, "device_attached", False):
            output.field("device", "attached")
        elif not usb.mounted:
            output.field("device", "not detected")
        if getattr(usb, "quick_mount_command", None):
            output.field("mount command", usb.quick_mount_command)
        output.field("layout", "ready" if usb.mercury_layout_present else "incomplete or absent")
    for check in report.permission_checks:
        status = "ok" if not check.needs_repair else f"needs repair — {check.detail}"
        output.field(check.label, status)
    output.write("")
    output.write("MariaDB")
    mariadb = report.mariadb
    output.field("client", mariadb.mariadb_client or "not found")
    output.field("mysqldump", mariadb.mysqldump_client or "not found")
    output.field("service", mariadb.service_state)
    output.field("socket", "available" if mariadb.socket_available else "unavailable")
    output.field("config", "present" if mariadb.config_present else "missing")
    if mariadb.configured_user:
        output.field("configured user", mariadb.configured_user)
    if mariadb.connection_works is True:
        output.field("connection", "ok")
    elif mariadb.connection_works is False:
        output.field("connection", f"failed — {mariadb.connection_error or 'unknown'}")
    else:
        output.field("connection", "not probed")
    if report.source_databases:
        output.write("")
        output.write("Source databases")
        for db_check in report.source_databases:
            output.field(db_check.name, db_check.detail)
    output.write("")
    output.write("Backup History")
    output.field(
        "verified full backups",
        f"{report.verified_backup_count} of {report.verified_backup_total}",
    )
    if report.self_healed:
        output.write("")
        output.write("Self-healed")
        for line in report.self_healed:
            output.item(line)
    if report.warnings:
        output.write("")
        output.write("Warnings")
        for warning in report.warnings:
            output.item(warning)
    if report.rebuild_complete:
        output.write("")
        output.write("Rebuild")
        output.item("Complete — protected production databases are present on this host.")
    if report.cleanup_suggestions:
        output.write("")
        output.write("Cleanup suggestions")
        output.write("These are not run automatically.")
        for command in report.cleanup_suggestions:
            output.item(command)
    output.write("")
    output.write("Actionable Blockers")
    if report.blockers:
        for blocker in report.blockers:
            output.item(blocker)
    else:
        output.item("None — environment looks ready for dry-run operations.")
    output.write("")
    output.write("Recommended Next Step")
    output.item(report.recommended_next_step)
    if _repair_plan_warranted(report):
        from mercury.repair.usb import USB_REPAIR_COMMAND

        if not primary_active:
            output.item(f"Quick USB fix: {USB_REPAIR_COMMAND}")
        output.item("Detailed steps: ./run.sh doctor --repair-plan")


def _repair_plan_warranted(report: DoctorReport) -> bool:
    if any(check.needs_repair for check in report.permission_checks):
        return True
    repairable = {
        "local config not initialized",
        "Operator backup mount not detected",
    }
    if any(
        any(token in blocker for token in repairable)
        for blocker in report.blockers
    ):
        return True
    if any("missing operator-storage artifact paths" in warning for warning in report.warnings):
        return True
    if any("prefer a dedicated unix_socket" in warning for warning in report.warnings):
        return True
    if any("not writable" in blocker for blocker in report.blockers):
        return True
    if any("MariaDB auth failed" in blocker for blocker in report.blockers):
        return True
    if any("service is not running" in blocker for blocker in report.blockers):
        return True
    return False


def print_repair_plan(report: DoctorReport) -> None:
    output.heading("Mercury repair plan")
    output.write("These commands are not run automatically. Review and execute manually.")
    output.write("")
    for title, commands in build_repair_plan(report):
        output.write(title)
        for command in commands:
            output.write(f"  {command}")
        output.write("")
