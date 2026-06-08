"""Interactive production backup menu (option 3)."""

from __future__ import annotations

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import run_backup_batch
from mercury.backup.terminal.batch import print_backup_batch_result
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.database.backup_planning import BackupPlanDryRun, build_backup_plan_from_inventory
from mercury.database.discovery import discover, discover_demo
from mercury.database.core.classifier import DatabaseRole, classify_database
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.backup.terminal.verify import print_verify_menu_summary, run_verify_all_for_menu

BACKUP_SCREEN_TITLE = "Backup Plan"


def read_backup_choice() -> str | None:
    return read_submenu_choice()


def _group_sources(plan: BackupPlanDryRun) -> tuple[list[str], list[str]]:
    production: list[str] = []
    shared: list[str] = []
    for name in plan.backup_sources:
        if classify_database(name).role == DatabaseRole.SHARED_AUTHORITY:
            shared.append(name)
        else:
            production.append(name)
    return production, shared


def _load_plan() -> BackupPlanDryRun:
    inventory = discover("live") if should_probe_database_status() else discover_demo()
    return build_backup_plan_from_inventory(inventory)


def _render_backup_screen(plan: BackupPlanDryRun, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(BACKUP_SCREEN_TITLE)
    policy = load_execution_policy()
    live_allowed = policy.live_execution_allowed()
    production_sources, shared_authority_sources = _group_sources(plan)
    out_of_scope = [item.name for item in plan.excluded if "Out of active Mercury scope" in item.reason]
    excluded_dev = [
        item.name
        for item in plan.excluded
        if item.role == DatabaseRole.DEVELOPMENT.value
        and "Out of active Mercury scope" not in item.reason
    ]
    display_screen.write_fields(
        {
            "Backup root": str(policy.backup_root.resolve()),
            "Backup root state": policy.backup_root_state(),
            "Mode": "live" if live_allowed else "dry-run",
            "Source databases": len(plan.backup_sources),
        }
    )
    display_screen.write_blank()
    display_screen.write_summary("Production sources")
    if production_sources:
        display_screen.write_table(["DATABASE"], [[name] for name in production_sources], max_col_widths=[40])
    else:
        display_screen.write_status("warn", "No production sources in scope.")
    display_screen.write_blank()
    display_screen.write_summary("Shared authority sources")
    if shared_authority_sources:
        display_screen.write_table(
            ["DATABASE", "SYNC"],
            [[name, "backup-only"] for name in shared_authority_sources],
            max_col_widths=[36, 18],
        )
    else:
        display_screen.write_status("warn", "No shared authority sources in scope.")
    display_screen.write_blank()
    display_screen.write_summary("Excluded development targets")
    if excluded_dev:
        display_screen.write_table(["DATABASE"], [[name] for name in excluded_dev], max_col_widths=[40])
    else:
        display_screen.write_summary("(none)")
    if out_of_scope:
        display_screen.write_blank()
        display_screen.write_summary("Out of scope")
        display_screen.write_table(["DATABASE"], [[name] for name in out_of_scope], max_col_widths=[40])
    display_screen.write_blank()
    display_screen.write_summary(
        "android_permission_intel is backup-only. Sync is not applicable by design."
    )
    display_screen.write_blank()
    options: list[tuple[str, str]] = [("1", "Rescan plan")]
    if plan.backup_sources:
        run_label = "Run full backup" if live_allowed else "Run full backup (live mode required)"
        if not live_allowed:
            run_label = f"{run_label} (recommended)"
        options.append(("2", run_label))
        options.append(("3", "Verify on-disk backups"))
    render_submenu(options)


def _run_backup(plan: BackupPlanDryRun) -> None:
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=execute,
        live=should_probe_database_status(),
        sources=list(plan.backup_sources),
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
            display_screen.write_summary(f"Plan refreshed — {len(plan.backup_sources)} source database(s).")
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
