"""Interactive prod→dev sync menu (option 4)."""

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
from mercury.sync.selection import select_sync_entries
from mercury.sync.terminal.runner import print_sync_batch_result
from mercury.sync.readiness import SyncReadinessReport, build_sync_readiness_report
from mercury.sync.terminal.readiness import _pair_route_label, print_sync_readiness_report
from mercury.sync.sync_plan import build_sync_plan_demo
from mercury.reporting.terminal.plan import print_sync_plan
from mercury.backup.verification import verify_backup_directory
from mercury.backup.terminal.verify import print_verification_result

SYNC_SCREEN_TITLE = "Production sync readiness"
SYNC_SCREEN_SUBTITLE = (
    "Refresh development databases from verified production operator backups only."
)


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


def _recommended_option_suffix(report: SyncReadinessReport, *, live_allowed: bool) -> str:
    if report.ready_count and not report.blocked_count:
        return " (recommended)"
    if report.ready_count and report.blocked_count and live_allowed:
        return " (ready pairs only)"
    if report.blocked_count and not report.ready_count:
        return " (recommended)"
    return ""


def _sync_submenu_options(report: SyncReadinessReport) -> list[tuple[str, str]]:
    policy = load_execution_policy()
    live_allowed = policy.live_execution_allowed()
    options: list[tuple[str, str]] = [("1", "Recheck Database Sync Status")]
    blocked = _blocked_prod_sources(report)
    ready = _ready_entries(report)
    if blocked:
        label = "Prepare production backups"
        if not live_allowed:
            label = f"{label} (preview only)"
        options.append(("2", f"{label}{_recommended_option_suffix(report, live_allowed=live_allowed)}"))
    if ready:
        sync_label = "Sync All Ready Databases" if live_allowed else "Preview All Ready Databases"
        sync_key = "2" if not blocked else "3"
        suffix = " (recommended)" if report.ready_count and not report.blocked_count else ""
        if report.ready_count and report.blocked_count and live_allowed:
            suffix = " (ready pairs only)"
        options.append((sync_key, f"{sync_label}{suffix}"))
        if report.ready_count > 1:
            single_label = "Sync One Ready Pair" if live_allowed else "Preview One Ready Pair"
            single_key = "3" if not blocked else "4"
            options.append((single_key, single_label))
    verify_key = "4" if not blocked else "5"
    options.append((verify_key, "Verify Dev Targets Against Prod Backups"))
    return options


def _render_sync_screen(report: SyncReadinessReport, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(SYNC_SCREEN_TITLE)
        display_screen.write_summary(SYNC_SCREEN_SUBTITLE)
        display_screen.write_blank()
    live_allowed = load_execution_policy().live_execution_allowed()
    if not live_allowed and report.blocked_count > 0:
        display_screen.write_status(
            "warn",
            "Sync execution is disabled in config; preview only until destructive sync is enabled.",
        )
    elif not live_allowed and report.ready_count > 0:
        display_screen.write_status(
            "warn",
            "Sync execution is disabled in config; ready pairs can be previewed only.",
        )
    print_sync_readiness_report(report, compact=True, menu=True, live_allowed=live_allowed)
    display_screen.write_blank()
    render_submenu(_sync_submenu_options(report), indent=0)


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
        display_screen.write_summary("Result: dry-run only; no files were written.")
        display_screen.write_bullets(
            [
                "Enable sync execution in config/local.toml when ready.",
                "Then run Prepare again here, verify with [3], and sync ready pairs.",
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
        display_screen.write_blank()
        display_screen.write_summary("Prod→dev sync will overwrite these development databases:")
        for entry in ready:
            age = f" · backup {entry.backup_age}" if entry.backup_age else ""
            fresh = f" · {entry.backup_freshness}" if entry.backup_freshness else ""
            display_screen.write_status("warn", f"{_pair_route_label(entry)}{age}{fresh}")
        display_screen.write_bullets(
            [
                "Production databases are never modified.",
                "Each dev target is dropped and recreated from its verified operator backup.",
            ]
        )
        display_screen.write_blank()
        if not menu_prompts.ask_confirmation_phrase(
            SYNC_DEV_CONFIRMATION_PHRASE,
            action="sync into dev",
        ):
            display_screen.write_summary("Sync cancelled.")
            return

    batch = run_sync_batch(ready, execute=execute, policy=policy)
    print_sync_batch_result(batch, compact=True)


def _run_sync_for_one_ready(report: SyncReadinessReport) -> None:
    ready = _ready_entries(report)
    if not ready:
        menu_display.write_status("warn", "No ready pairs — choose Prepare or Rescan first.")
        return
    if len(ready) == 1:
        _run_sync_for_ready(report)
        return

    rows = []
    for index, entry in enumerate(ready, start=1):
        backup = entry.backup_age or "—"
        rows.append([str(index), entry.project or "—", _pair_route_label(entry), backup])
    display_screen.write_blank()
    display_screen.write_compact_table(
        ["#", "PROJECT", "PROD → DEV", "BACKUP"],
        rows,
        min_col_widths=[2, 10, 24, 10],
    )
    choice = menu_prompts.ask_stripped("\nPair number to sync: ")
    if choice is None or not choice:
        display_screen.write_summary("Sync cancelled.")
        return
    try:
        selected_index = int(choice)
    except ValueError:
        menu_display.write_status("warn", f"Invalid pair number: {choice!r}")
        return
    if selected_index < 1 or selected_index > len(ready):
        menu_display.write_status("warn", f"Pair number out of range: {selected_index}")
        return

    selected = ready[selected_index - 1]
    filtered = select_sync_entries(
        report.entries,
        source=selected.prod,
        target=selected.expected_dev,
    )
    _run_sync_for_ready(report.model_copy(update={"entries": filtered, "ready_count": 1}))


def _refresh_report(report: SyncReadinessReport) -> SyncReadinessReport:
    refreshed = _load_report()
    return refreshed if refreshed is not None else report


def _verify_sync_targets() -> None:
    from mercury.sync.verification import build_sync_verification_report
    from mercury.sync.terminal.verification import print_sync_verification_report

    print_sync_verification_report(
        build_sync_verification_report(live=should_probe_database_status()),
        compact=True,
    )


def run_sync_menu(*, interactive: bool = True) -> None:
    """Show sync readiness and an action submenu until the user returns."""
    report = _load_report()
    if report is None:
        print_sync_plan(build_sync_plan_demo(), compact=True)
        return

    show_title = True
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

        blocked_present = bool(_blocked_prod_sources(report))

        if choice == "2" and blocked_present:
            _prepare_production_backups(report)
            report = _refresh_report(report)
            show_title = pause_and_redraw()
            continue

        if (choice == "2" and not blocked_present) or (choice == "3" and blocked_present):
            _run_sync_for_ready(report)
            report = _refresh_report(report)
            show_title = pause_and_redraw()
            continue

        if (choice == "3" and not blocked_present) or (choice == "4" and blocked_present):
            _run_sync_for_one_ready(report)
            report = _refresh_report(report)
            show_title = pause_and_redraw()
            continue

        if (choice == "4" and not blocked_present) or (choice == "5" and blocked_present):
            _verify_sync_targets()
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
