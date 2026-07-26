"""Interactive Restore and Disaster Recovery dashboard (Main Menu [5])."""

from __future__ import annotations

from pathlib import Path

from mercury import output
from mercury.core.execution_policy import load_execution_policy
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, render_submenu
from mercury.restore.check_cleanup import cleanup_restorecheck_databases
from mercury.restore.check_plan import RestoreCheckPlan
from mercury.restore.dashboard import (
    RecoveryDashboard,
    build_recovery_dashboard,
    pending_restore_check_plans,
    selected_restore_check_plans,
)
from mercury.restore.restore_runner import execute_restore_into_database
from mercury.restore.terminal.check_cleanup import print_restorecheck_cleanup_batch
from mercury.restore.terminal.runner import print_restore_execution_result
from mercury.terminal import screen as display_screen
from mercury.terminal.table import Table, TableStyle

DASHBOARD_TITLE = "Restore and Disaster Recovery"


def _write_focus(dashboard: RecoveryDashboard) -> None:
    from mercury.terminal.theme import (
        active_styles,
        colors_enabled,
        hint_text,
        markup,
        status_badge,
    )

    if dashboard.restore_checks_pending:
        next_line = (
            f"Next: complete {dashboard.restore_checks_pending} pending restore-checks."
        )
        pending_line = f"Pending: {', '.join(dashboard.pending_names)}"
        if colors_enabled():
            styles = active_styles()
            output.write(
                f"{status_badge('warn')} {markup(next_line, styles.recommended)}"
            )
            output.write(markup(pending_line, styles.value))
        else:
            output.write(next_line)
            output.write(pending_line)
        if dashboard.runnable_pending:
            output.write(
                hint_text(
                    "Imports into disposable _restorecheck_* databases only — never *_prod."
                )
            )
        else:
            output.write(
                hint_text(
                    "Pending development restore-check lanes are listed for status; "
                    "production runnable gaps use [1]."
                )
            )
        return

    ready = "Next: recovery scope is ready for this host."
    if colors_enabled():
        output.write(f"{status_badge('ok')} {markup(ready, active_styles().ok)}")
    else:
        output.write(ready)


def _render_dashboard(dashboard: RecoveryDashboard, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(DASHBOARD_TITLE)

    display_screen.write_fields(
        {
            "Readiness": dashboard.readiness,
            "Production": (
                f"{dashboard.production_backed_up}/{dashboard.production_total} backed up"
            ),
            "Development": (
                f"{dashboard.development_backed_up}/{dashboard.development_total} backed up"
            ),
            "Required DBs": (
                f"{dashboard.production_backed_up + dashboard.development_backed_up}/7 backed up "
                f"· {dashboard.restore_checks_passed}/7 restore-checked"
            ),
            "Latest backup": dashboard.latest_backup_label,
            "Package": dashboard.package_line,
            "Runbooks": dashboard.runbooks_path,
        }
    )
    if dashboard.temp_restore_schemas:
        display_screen.write_fields(
            {
                "Temporary restore schemas": (
                    f"{len(dashboard.temp_restore_schemas)} present"
                )
            }
        )
    else:
        display_screen.write_summary("Temporary restore schemas: none")

    display_screen.write_blank()
    _write_focus(dashboard)
    display_screen.write_blank()

    rows = [
        [row.database, row.freshness, row.artifact, row.restore_check]
        for row in dashboard.rows
    ]
    table = Table.from_headers(
        ["DATABASE", "BACKUP", "ARTIFACT", "RESTORE-CHECK"],
        rows,
        style=TableStyle(indent=0),
        min_col_widths=[32, 10, 10, 12],
        max_col_widths=[40, 12, 14, 16],
    )
    display_screen.write_structured_table(table)
    display_screen.write_blank()

    options: list[tuple[str, str]] = []
    run_pending_label = "Run pending restore-checks"
    if dashboard.runnable_pending:
        run_pending_label = f"{run_pending_label}      recommended"
    options.append(("1", run_pending_label))
    options.append(("2", "Run restore-checks for selected databases"))
    if dashboard.temp_restore_schemas:
        options.append(
            (
                "3",
                f"Clean up restore-check databases ({len(dashboard.temp_restore_schemas)})",
            )
        )
    else:
        options.append(("3", "Clean up restore-check databases (none)"))
    options.append(("4", "Pinned and destination recovery"))
    options.append(("5", "Receiving workstation guide (also under [7])"))
    options.append(("6", "View receipts and verification history"))
    render_submenu(options, indent=0)


def _execute_plans(plans: list[RestoreCheckPlan]) -> None:
    if not plans:
        display_screen.write_status("warn", "No runnable restore-check plans selected.")
        return
    policy = load_execution_policy()
    if not policy.live_execution_allowed():
        display_screen.write_status(
            "warn",
            "Live mode required (dry_run=false and live_actions_enabled=true).",
        )
        for plan in plans:
            display_screen.write_summary(
                f"Would restore-check {plan.source_prod} → {plan.restore_target}"
            )
        return
    names = ", ".join(plan.source_prod for plan in plans)
    if not menu_prompts.ask_yes_no(
        f"Run restore-check for {len(plans)} database(s) ({names})?",
        default=False,
    ):
        display_screen.write_summary("Restore-check cancelled.")
        return
    for plan in plans:
        if not plan.dump_file or not plan.backup_directory:
            continue
        dump_path = Path(plan.backup_directory) / plan.dump_file
        result = execute_restore_into_database(
            target_database=plan.restore_target,
            dump_path=dump_path,
            source_database=plan.source_prod,
            execute=True,
            policy=policy,
            recreate_target=True,
            cleanup_after_success=True,
        )
        print_restore_execution_result(result, compact=True)


def _run_selected(dashboard: RecoveryDashboard) -> None:
    raw = menu_prompts.ask_stripped(
        "Databases (comma-separated production names, or Enter to cancel): "
    )
    if not raw:
        display_screen.write_summary("Selection cancelled.")
        return
    names = [part.strip() for part in raw.split(",") if part.strip()]
    plans = selected_restore_check_plans(dashboard, names)
    _execute_plans(plans)


def _cleanup(dashboard: RecoveryDashboard) -> None:
    if not dashboard.temp_restore_schemas:
        display_screen.write_summary("Temporary restore schemas: none")
        return
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    if execute and not menu_prompts.ask_yes_no(
        f"Drop {len(dashboard.temp_restore_schemas)} _restorecheck_* database(s)?",
        default=False,
    ):
        display_screen.write_summary("Cleanup cancelled.")
        return
    batch = cleanup_restorecheck_databases(
        dashboard.temp_restore_schemas,
        execute=execute,
    )
    print_restorecheck_cleanup_batch(batch, compact=True)


def _pinned_recovery_card() -> None:
    display_screen.open_screen("Pinned / destination recovery")
    display_screen.write_summary("Destination recovery (package-pinned schemas):")
    display_screen.write_summary("  ./run.sh restore-check destination --help")
    display_screen.write_blank()
    display_screen.write_summary("Exact restore-check by backup ID:")
    display_screen.write_summary(
        "  ./run.sh restore-check run --db <prod> --backup-id <id>"
    )
    display_screen.write_blank()
    display_screen.write_summary(
        "Production cutover preview/execute stays under Deployment and handoff [7]."
    )


def _receiving_guide() -> None:
    from mercury.handoff.receiver import build_receiver_handoff_guide
    from mercury.handoff.terminal import print_receiver_handoff_guide

    display_screen.write_summary(
        "Primary home for receiving-workstation handoff is Deployment and handoff [7]."
    )
    print_receiver_handoff_guide(checklist=build_receiver_handoff_guide())


def _receipts_history() -> None:
    from mercury.backup.full_backup_receipts import (
        INVALID_MAINTENANCE_CLASS,
        plan_quarantine_invalid_full_backup_receipts,
    )
    from mercury.core.usb_mount import resolve_operator_mount

    mount = resolve_operator_mount()
    plan = plan_quarantine_invalid_full_backup_receipts(mount)
    display_screen.open_screen("Full-backup receipt plan")
    display_screen.write_fields(
        {
            "Mount": str(plan.mount_root),
            "Quarantine dir": str(plan.quarantine_dir),
            "Governed": plan.governed_count,
            "Invalid maintenance": plan.invalid_count,
            "Total scanned": len(plan.entries),
        }
    )
    invalid = [
        entry for entry in plan.entries if entry.classification == INVALID_MAINTENANCE_CLASS
    ]
    if invalid:
        display_screen.write_blank()
        display_screen.write_summary(f"Invalid maintenance receipts: {len(invalid)}")
    display_screen.write_blank()
    display_screen.write_summary("Observe-only. Quarantine execute stays CLI-gated.")


def run_recovery_dashboard(*, interactive: bool = True) -> None:
    """Main Menu [5] — consolidated recovery dashboard (observe-only until execute)."""
    dashboard = build_recovery_dashboard()
    show_title = True
    while True:
        _render_dashboard(dashboard, show_title=show_title)
        show_title = False
        if not interactive:
            return
        choice = menu_prompts.ask_stripped("\nChoice: ")
        if choice is None or choice in {"", "0"}:
            return
        if choice == "1":
            _execute_plans(pending_restore_check_plans(dashboard))
            dashboard = build_recovery_dashboard()
            show_title = pause_and_redraw()
            continue
        if choice == "2":
            _run_selected(dashboard)
            dashboard = build_recovery_dashboard()
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            _cleanup(dashboard)
            dashboard = build_recovery_dashboard()
            show_title = pause_and_redraw()
            continue
        if choice == "4":
            _pinned_recovery_card()
            show_title = pause_and_redraw()
            continue
        if choice == "5":
            _receiving_guide()
            show_title = pause_and_redraw()
            continue
        if choice == "6":
            _receipts_history()
            show_title = pause_and_redraw()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))
