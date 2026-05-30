"""Interactive menu shell (seed: placeholders or dry-run only)."""

from mercury.env_probe import probe_environment
from mercury import output
from mercury.runtime import operator_status


MENU_TITLE = "MERCURY"
MENU_SUBTITLE = "Database Backup, Sync, and Disaster Recovery Utility"

MENU_ITEMS: list[tuple[str, str]] = [
    ("1", "Environment Check"),
    ("2", "Discover / Classify Databases"),
    ("3", "Backup Production Databases"),
    ("4", "Export Schema-Only Copies"),
    ("5", "Verify Backups"),
    ("6", "Sync Production -> Development"),
    ("7", "Restore Test / Disaster Recovery Check"),
    ("8", "Reports / Backup History"),
    ("0", "Exit"),
]

PLACEHOLDERS: dict[str, str] = {
    "7": "Restore test / DR check not implemented. Future: restore to _restorecheck_* only.",
}


def render_status_block() -> str:
    status = operator_status()
    return (
        "Status:\n"
        f"- Mode: {status['mode']}\n"
        f"- Database: {status['database']}\n"
        f"- Backup root: {status['backup_root']}\n"
        f"- Safety: {status['safety']}"
    )


def render_menu_text() -> str:
    lines = [
        MENU_TITLE,
        MENU_SUBTITLE,
        "",
        render_status_block(),
        "",
        "Menu:",
    ]
    for key, label in MENU_ITEMS:
        lines.append(f"[{key}] {label}")
    return "\n".join(lines)


def run_discover_databases() -> None:
    from mercury.database import (
        MariaDbConfigError,
        MariaDbDriverMissingError,
        MariaDbLiveError,
        discover,
        discover_demo,
        print_inventory,
    )

    try:
        output.write("Discover / Classify — live read-only (SHOW DATABASES)")
        output.write("")
        print_inventory(discover("live"))
        return
    except MariaDbConfigError as exc:
        output.write(f"Live discovery unavailable: {exc}")
        output.write("Showing demo catalog instead (dry-run).")
        output.write("")
    except (MariaDbDriverMissingError, MariaDbLiveError) as exc:
        output.write(f"Live discovery failed: {exc}")
        output.write("Showing demo catalog instead.")
        output.write("")

    print_inventory(discover_demo())


def run_verify_plan() -> None:
    from mercury.verification import build_verification_plan_demo
    from mercury.verify_display import print_verification_plan

    output.write("Verify Backups — preview/dry-run only")
    output.write("")
    print_verification_plan(build_verification_plan_demo())


def run_reports_and_history() -> None:
    from mercury.backup_list import build_demo_backup_list
    from mercury.report_preview import build_report_preview
    from mercury.safety import BACKUP_KIND_FULL
    from mercury.verify_display import print_demo_backup_list, print_report_preview

    output.write("Reports / Backup History — preview/dry-run only")
    output.write("")
    print_demo_backup_list(build_demo_backup_list())
    output.write("")
    output.write("Sample report (erebus_threat_intel_prod full):")
    output.write("")
    print_report_preview(
        build_report_preview("erebus_threat_intel_prod", BACKUP_KIND_FULL)
    )


def run_protection_report() -> None:
    from mercury.protection_report import build_protection_report, format_protection_report

    output.write(format_protection_report(build_protection_report()))


def run_schema_backup_plan() -> None:
    from mercury.schema_backup_plan import build_schema_backup_plan_demo
    from mercury.plan_display import print_schema_backup_plan

    output.write("Export Schema-Only Copies — dry-run only")
    output.write("(no mariadb-dump, no live DB, no files written)")
    output.write("")
    print_schema_backup_plan(build_schema_backup_plan_demo())


def run_sync_plan() -> None:
    from mercury.sync_plan import build_sync_plan_demo
    from mercury.plan_display import print_sync_plan

    print_sync_plan(build_sync_plan_demo())


def run_backup_plan_dry_run() -> None:
    from mercury.database.planning import build_demo_backup_plan
    from mercury.backup_display import print_backup_plan

    print_backup_plan(build_demo_backup_plan())


def run_environment_check() -> None:
    result = probe_environment()
    output.write(render_status_block())
    output.write()
    output.field("python", result.python_version)
    output.field("platform", f"{result.platform_system} {result.platform_release}")
    output.field("repo_root", result.repo_root)
    for note in result.notes:
        output.bullet(note)


def handle_menu_choice(choice: str) -> bool:
    """Handle one menu selection. Returns False to exit."""
    if choice == "0":
        output.write("Exiting Mercury.")
        return False
    if choice == "1":
        run_environment_check()
        return True
    if choice == "2":
        run_discover_databases()
        return True
    if choice == "3":
        run_backup_plan_dry_run()
        return True
    if choice == "4":
        run_schema_backup_plan()
        return True
    if choice == "5":
        run_verify_plan()
        return True
    if choice == "6":
        run_sync_plan()
        return True
    if choice == "8":
        run_reports_and_history()
        return True
    placeholder = PLACEHOLDERS.get(choice)
    if placeholder:
        output.write()
        output.write(f"Not yet implemented: {placeholder}")
        return True
    output.write("Invalid choice. Enter 0-8.")
    return True


def run_menu(interactive: bool = True) -> None:
    """Show the Mercury menu. In interactive mode, loop until exit."""
    output.write(render_menu_text())

    if not interactive:
        return

    while True:
        try:
            choice = input("\nSelect option [0-8]: ").strip()
        except (EOFError, KeyboardInterrupt):
            output.write()
            output.write("Exiting Mercury.")
            break
        if not handle_menu_choice(choice):
            break
        output.write()
