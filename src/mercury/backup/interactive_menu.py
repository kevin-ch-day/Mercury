"""Interactive production backup menu (option 3)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import BackupBatchResult, run_backup_batch
from mercury.backup.terminal.batch import print_backup_batch_result
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.backup.terminal.verify import print_verify_menu_summary, run_verify_all_for_menu

BACKUP_SCREEN_TITLE = "Backup Production Databases"


def read_backup_choice() -> str | None:
    return read_submenu_choice()


def _load_plan() -> BackupBatchResult:
    return run_backup_batch(
        BACKUP_KIND_FULL,
        execute=False,
        live=should_probe_database_status(),
    )


def _render_backup_screen(plan: BackupBatchResult, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(BACKUP_SCREEN_TITLE)
    policy = load_execution_policy()
    live_allowed = policy.live_execution_allowed()
    display_screen.write_fields(
        {
            "policy": "live" if live_allowed else "dry-run",
            "sources": len(plan.sources),
        }
    )
    if plan.sources:
        display_screen.write_table(
            ["DATABASE"],
            [[name] for name in plan.sources],
            max_col_widths=[40],
        )
    else:
        menu_display.write_status("warn", "No backup sources in inventory.")
    display_screen.write_blank()
    options: list[tuple[str, str]] = [("1", "Rescan plan")]
    if plan.sources:
        run_label = "Run full backup" if live_allowed else "Run full backup (needs live mode)"
        if not live_allowed:
            run_label = f"{run_label} (recommended)"
        options.append(("2", run_label))
        options.append(("3", "Verify on-disk backups"))
    render_submenu(options)


def _run_backup(plan: BackupBatchResult) -> None:
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=execute,
        live=should_probe_database_status(),
        sources=list(plan.sources),
    )
    print_backup_batch_result(batch, compact=True, menu=True)
    if batch.executed_count:
        display_screen.write_summary(f"Wrote {batch.executed_count} backup(s).")


def run_backup_menu(*, interactive: bool = True) -> None:
    plan = _load_plan()
    show_title = False
    while True:
        _render_backup_screen(plan, show_title=show_title)
        show_title = False
        if not interactive:
            return

        choice = read_backup_choice()
        if choice is None:
            return
        if choice == "0":
            return

        if choice == "1":
            plan = _load_plan()
            display_screen.write_summary(f"Plan refreshed — {len(plan.sources)} source(s).")
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            _run_backup(plan)
            plan = _load_plan()
            show_title = pause_and_redraw()
            continue

        if choice == "3":
            summary = run_verify_all_for_menu(update_manifest=True)
            print_verify_menu_summary(summary)
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
