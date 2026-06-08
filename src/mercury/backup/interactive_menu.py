"""Interactive production backup menu (option 1)."""

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

BACKUP_SCREEN_TITLE = "Backup Plan"


def _backup_target_label(policy) -> str:
    state = policy.backup_root_state()
    if state == "usb-mounted":
        return "USB mounted"
    if state == "usb not mounted":
        return "USB not mounted"
    if state == "repo-local fallback":
        return "repo-local fallback"
    if state == "low free space":
        return "USB mounted; low free space"
    return state.replace("-", " ")


def read_backup_choice() -> str | None:
    return read_submenu_choice()


def _plan_rows(plan: BackupPlanDryRun) -> list[list[str]]:
    rows: list[list[str]] = []
    dev_targets = {
        item.name
        for item in plan.excluded
        if item.role == DatabaseRole.DEVELOPMENT.value
        and "Out of active Mercury scope" not in item.reason
    }

    for name in sorted(plan.backup_sources):
        classification = classify_database(name)
        if classification.role == DatabaseRole.SHARED_AUTHORITY:
            rows.append([name, "shared", "backup", "n/a"])
        else:
            rows.append([name, "prod", "backup", "dev target"])

    for name in sorted(dev_targets):
        rows.append([name, "dev", "skip", "refresh target"])

    return rows


def _load_plan() -> BackupPlanDryRun:
    inventory = discover("live") if should_probe_database_status() else discover_demo()
    return build_backup_plan_from_inventory(inventory)


def _render_backup_screen(plan: BackupPlanDryRun, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(BACKUP_SCREEN_TITLE)
    policy = load_execution_policy()
    live_allowed = policy.live_execution_allowed()
    display_screen.write_fields(
        {
            "Target": str(policy.backup_root.resolve()),
            "Mode": "LIVE" if live_allowed else "DRY RUN",
            "Action": "full backup",
        }
    )
    display_screen.write_blank()
    rows = _plan_rows(plan)
    if rows:
        display_screen.write_compact_table(
            ["DATABASE", "ROLE", "PLAN", "SYNC"],
            rows,
            min_col_widths=[28, 8, 8, 14],
        )
    else:
        display_screen.write_status("warn", "No databases in active backup scope.")
    options: list[tuple[str, str]] = [("1", "Refresh")]
    if plan.backup_sources:
        run_label = "Run full backup" if live_allowed else "Run full backup (live mode required)"
        options.append(("2", run_label))
    display_screen.write_blank()
    render_submenu(options, indent=0)


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
    show_title = True
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

        output.write(menu_prompts.invalid_choice_message(choice))
