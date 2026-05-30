"""Interactive menu shell."""

from mercury.env_probe import probe_environment
from mercury import output
from mercury.core.runtime import should_probe_database_status
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


def render_status_block(*, probe_database: bool = False) -> str:
    status = operator_status(probe_database=probe_database)
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
        render_status_block(probe_database=should_probe_database_status()),
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
    from mercury.backup.batch import resolve_batch_sources
    from mercury.backup.locate import find_latest_backup_directory
    from mercury.core.execution_policy import load_execution_policy
    from mercury.verification import (
        build_verification_plan_demo,
        verify_backup_directory,
    )
    from mercury.verify_display import print_verification_plan, print_verification_result

    policy = load_execution_policy()
    sources = resolve_batch_sources(live=should_probe_database_status())
    verified_any = False

    for db in sources:
        backup_dir = find_latest_backup_directory(policy.backup_root, db)
        if backup_dir is None:
            continue
        output.write(f"Verifying on-disk backup: {db}")
        output.write(f"  directory: {backup_dir}")
        output.write("")
        result = verify_backup_directory(backup_dir)
        print_verification_result(result)
        output.write("")
        verified_any = True

    if verified_any:
        output.write("Tip: mercury backup verify-all --update-manifest")
        return

    output.write("Verify Backups — no on-disk backups found under backup_root.")
    output.write(f"  backup_root: {policy.backup_root}")
    output.write("")
    output.write("Run: mercury backup batch --kind full [--execute]")
    output.write("Then: mercury backup verify-all --update-manifest")
    output.write("")
    output.write("Preview / dry-run verification plan:")
    output.write("")
    print_verification_plan(build_verification_plan_demo())


def run_reports_and_history() -> None:
    from mercury.core.execution_policy import load_execution_policy
    from mercury.protection_report import build_protection_report, format_protection_report

    from mercury.backup.list import build_on_disk_backup_list
    from mercury.verify_display import print_on_disk_backup_list

    policy = load_execution_policy()
    backup_list = build_on_disk_backup_list(policy.backup_root)
    if backup_list.records:
        print_on_disk_backup_list(backup_list)
    else:
        output.write("No on-disk backups yet.")
        output.write(f"  backup_root: {policy.backup_root}")
        output.write("")

    output.write("Protection status:")
    output.write("")
    live = should_probe_database_status()
    try:
        output.write(format_protection_report(build_protection_report(live=live, probe_database=live)))
    except Exception as exc:
        output.write(format_protection_report(build_protection_report()))


def run_schema_backup_plan() -> None:
    from mercury.database import MariaDbConfigError, MariaDbLiveError, try_load_mariadb_config
    from mercury.plan_display import print_schema_backup_plan
    from mercury.schema_backup_plan import (
        build_schema_backup_plan_demo,
        build_schema_backup_plan_live,
    )

    output.write("Export Schema-Only Copies — planning")
    output.write("")
    if try_load_mariadb_config() is not None:
        try:
            print_schema_backup_plan(build_schema_backup_plan_live())
            output.write("")
            output.write("Run one DB: mercury backup run --db <prod> --kind schema_only [--execute]")
            return
        except (MariaDbConfigError, MariaDbLiveError) as exc:
            output.write(f"Live schema plan failed: {exc}")
            output.write("")
    print_schema_backup_plan(build_schema_backup_plan_demo())


def run_sync_plan() -> None:
    from mercury.database import MariaDbConfigError, MariaDbLiveError, try_load_mariadb_config
    from mercury.plan_display import print_sync_plan
    from mercury.sync.readiness import build_sync_readiness_report
    from mercury.sync.readiness_display import print_sync_readiness_report
    from mercury.sync_plan import build_sync_plan_demo, build_sync_plan_live

    if try_load_mariadb_config() is not None:
        try:
            print_sync_plan(build_sync_plan_live())
            output.write("")
            print_sync_readiness_report(build_sync_readiness_report(live=True))
            return
        except (MariaDbConfigError, MariaDbLiveError) as exc:
            output.write(f"Live sync plan failed: {exc}")
            output.write("")
    print_sync_plan(build_sync_plan_demo())


def run_backup_batch_menu() -> None:
    from mercury.backup.batch import run_backup_batch
    from mercury.backup.batch_display import print_backup_batch_result
    from mercury.core.safety import BACKUP_KIND_FULL

    output.write("Backup Production Databases — batch dry-run plan")
    output.write("(use CLI for live execution: mercury backup batch --execute)")
    output.write("")
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=False,
        live=should_probe_database_status(),
    )
    print_backup_batch_result(batch)


def run_restore_check_menu() -> None:
    from mercury.backup.batch import resolve_batch_sources
    from mercury.restore.check import build_restore_check_plan
    from mercury.restore.display import print_restore_check_plan

    sources = resolve_batch_sources(live=should_probe_database_status())
    if not sources:
        output.write("No backup sources found.")
        return

    output.write("Restore Test / DR Check — dry-run plans")
    output.write("")
    for prod in sources[:3]:
        print_restore_check_plan(build_restore_check_plan(prod))
        output.write("")
    if len(sources) > 3:
        output.write(f"... and {len(sources) - 3} more. Use: mercury restore-check plan --db <prod>")


def run_environment_check() -> None:
    from mercury.database import (
        MariaDbConfigError,
        MariaDbDriverMissingError,
        MariaDbLiveError,
        probe_mariadb_server,
        try_load_mariadb_config,
    )
    from mercury.database.display_ping import print_server_probe

    result = probe_environment()
    output.write(render_status_block(probe_database=True))
    output.write()
    output.field("python", result.python_version)
    output.field("platform", f"{result.platform_system} {result.platform_release}")
    output.field("repo_root", result.repo_root)
    for note in result.notes:
        output.bullet(note)

    if try_load_mariadb_config() is not None:
        output.write()
        output.write("MariaDB config detected — running read-only server probe...")
        output.write("")
        try:
            print_server_probe(probe_mariadb_server())
        except MariaDbConfigError as exc:
            output.write(f"Config error: {exc}")
        except MariaDbDriverMissingError as exc:
            output.write(str(exc))
        except MariaDbLiveError as exc:
            output.write(f"Connection failed: {exc}")
    else:
        output.write()
        output.write("No MariaDB config — run: mercury config init")
        output.write("For Fedora local dev, use use_client=true + unix_socket in local.toml.")


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
        run_backup_batch_menu()
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
    if choice == "7":
        run_restore_check_menu()
        return True
    if choice == "8":
        run_reports_and_history()
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
