"""Interactive prod→dev sync menu (option 6)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import run_backup_batch
from mercury.backup.terminal.batch import print_backup_batch_result
from mercury.backup.find_latest_backup import find_latest_backup_directory
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_FULL, SYNC_DEV_CONFIRMATION_PHRASE
from mercury.database import MariaDbConfigError, MariaDbLiveError, try_load_mariadb_config
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.sync.sync_runner import run_sync_batch
from mercury.sync.terminal.runner import print_sync_batch_result
from mercury.sync.readiness import SyncReadinessReport, build_sync_readiness_report
from mercury.sync.terminal.readiness import print_sync_readiness_report
from mercury.sync.sync_plan import build_sync_plan_demo
from mercury.reporting.terminal.plan import print_sync_plan
from mercury.backup.verification import verify_backup_directory
from mercury.backup.terminal.verify import print_verification_result

SYNC_SCREEN_TITLE = "Production sync readiness"


def read_sync_choice() -> str | None:
    return read_submenu_choice()


def _load_report() -> SyncReadinessReport | None:
    if try_load_mariadb_config() is not None:
        try:
            return build_sync_readiness_report(live=True)
        except (MariaDbConfigError, MariaDbLiveError) as exc:
            menu_display.write_status("fail", f"Live sync readiness failed: {exc}")
            display_screen.write_blank()
    return None


def _blocked_prod_sources(report: SyncReadinessReport) -> list[str]:
    return [
        entry.prod
        for entry in report.entries
        if not entry.ready_for_sync_planning and entry.dev_listed
    ]


def _ready_entries(report: SyncReadinessReport):
    return [entry for entry in report.entries if entry.ready_for_sync_planning]


def _sync_submenu_options(report: SyncReadinessReport) -> list[tuple[str, str]]:
    policy = load_execution_policy()
    live_allowed = policy.live_execution_allowed()
    options: list[tuple[str, str]] = [("1", "Rescan readiness")]
    if _blocked_prod_sources(report):
        label = "Prepare production backups"
        if report.ready_count == 0:
            label = f"{label} (recommended)"
        if not live_allowed:
            label = f"{label} (live mode required)"
        options.append(("2", label))
    if _ready_entries(report):
        sync_label = f"Sync ready pairs ({report.ready_count})"
        if not live_allowed:
            sync_label = f"{sync_label} (live mode required)"
        options.append(("3", sync_label))
    return options


def _render_sync_screen(report: SyncReadinessReport, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(SYNC_SCREEN_TITLE)
    policy = load_execution_policy()
    live_allowed = policy.live_execution_allowed()
    display_screen.write_fields({"policy": "live" if live_allowed else "dry-run"})
    if not live_allowed and report.blocked_count > 0:
        display_screen.write_status(
            "warn",
            "Live mode is required for backup preparation and sync. Open Environment check -> Live mode guide.",
        )
    print_sync_readiness_report(report, compact=True, menu=True)
    display_screen.write_blank()
    render_submenu(_sync_submenu_options(report))


def _prepare_production_backups(report: SyncReadinessReport) -> None:
    sources = _blocked_prod_sources(report)
    if not sources:
        menu_display.write_status("warn", "No blocked pairs with dev targets to prepare.")
        return

    policy = load_execution_policy()
    execute = policy.live_execution_allowed()

    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=execute,
        live=should_probe_database_status(),
        sources=sources,
    )
    print_backup_batch_result(batch, compact=True, menu=True)

    if not execute:
        display_screen.write_blank()
        display_screen.write_summary("Dry-run only — no backup files were written.")
        display_screen.write_bullets(
            [
                "Enable live mode: main menu [1] Environment Check → [2] Live mode guide",
                "Then run Prepare again here, verify with [5], and sync ready pairs",
            ]
        )
        return

    display_screen.write_blank()
    verified = 0
    for database in sources:
        backup_dir = find_latest_backup_directory(policy.backup_root, database)
        if backup_dir is None:
            menu_display.write_status("warn", f"{database}: no on-disk backup after batch")
            continue
        result = verify_backup_directory(backup_dir, database=database, update_manifest=True)
        print_verification_result(result, compact=True)
        if result.verified:
            verified += 1
    display_screen.write_summary(f"Verified {verified} of {len(sources)} prepared source(s).")


def _run_sync_for_ready(report: SyncReadinessReport) -> None:
    ready = _ready_entries(report)
    if not ready:
        menu_display.write_status("warn", "No pairs ready — choose Prepare or Rescan first.")
        return

    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    if execute:
        display_screen.write_summary(
            f"Ready to sync {len(ready)} pair(s). Type {SYNC_DEV_CONFIRMATION_PHRASE!r} to confirm."
        )
        if not menu_prompts.ask_confirmation_phrase(
            SYNC_DEV_CONFIRMATION_PHRASE,
            action="sync into dev",
        ):
            display_screen.write_summary("Sync cancelled.")
            return

    batch = run_sync_batch(ready, execute=execute, policy=policy)
    print_sync_batch_result(batch, compact=True)


def _refresh_report(report: SyncReadinessReport) -> SyncReadinessReport:
    refreshed = _load_report()
    return refreshed if refreshed is not None else report


def run_sync_menu(*, interactive: bool = True) -> None:
    """Show sync readiness and an action submenu until the user returns."""
    report = _load_report()
    if report is None:
        print_sync_plan(build_sync_plan_demo(), compact=True)
        return

    show_title = False
    while True:
        _render_sync_screen(report, show_title=show_title)
        show_title = False
        if not interactive:
            return

        choice = read_sync_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            report = _refresh_report(report)
            display_screen.write_summary(
                f"Rescanned — {report.ready_count} ready, {report.blocked_count} blocked."
            )
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            _prepare_production_backups(report)
            report = _refresh_report(report)
            show_title = pause_and_redraw()
            continue

        if choice == "3":
            _run_sync_for_ready(report)
            report = _refresh_report(report)
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
