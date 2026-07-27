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


def _write_header_fields(fields: dict[str, str]) -> None:
    """Colon-free aligned rows (matches Backup Operations)."""
    from mercury.terminal.theme import dashboard_row

    if not fields:
        return
    label_width = max(len(name) for name in fields) + 2
    for name, value in fields.items():
        output.write(dashboard_row(name, value, label_width=label_width))


def _live_mode_ready() -> bool:
    return load_execution_policy().live_execution_allowed()


def _write_focus(dashboard: RecoveryDashboard) -> None:
    from mercury.terminal.theme import (
        active_styles,
        colors_enabled,
        hint_text,
        markup,
        status_badge,
    )

    if dashboard.runnable_pending:
        count = len(dashboard.runnable_pending)
        next_line = f"Next: Run pending restore-checks [1] ({count})"
        pending_line = f"Pending: {', '.join(dashboard.runnable_pending)}"
        if colors_enabled():
            styles = active_styles()
            output.write(
                f"{status_badge('warn')} {markup(next_line, styles.recommended)}"
            )
            output.write(markup(pending_line, styles.value))
        else:
            output.write(next_line)
            output.write(pending_line)
        if not _live_mode_ready():
            output.write(
                hint_text(
                    "Live mode required first: dry_run=false and live_actions_enabled=true."
                )
            )
        else:
            output.write(
                hint_text(
                    "Imports into disposable _restorecheck_* databases only — never *_prod."
                )
            )
        return

    if dashboard.pending_names:
        next_line = (
            f"Next: {len(dashboard.pending_names)} production restore-check(s) blocked."
        )
        pending_line = f"Pending: {', '.join(dashboard.pending_names)}"
        if colors_enabled():
            styles = active_styles()
            output.write(f"{status_badge('warn')} {markup(next_line, styles.warn)}")
            output.write(markup(pending_line, styles.value))
        else:
            output.write(next_line)
            output.write(pending_line)
        return

    ready = "Next: production restore-checks are complete on this host."
    if colors_enabled():
        output.write(f"{status_badge('ok')} {markup(ready, active_styles().ok)}")
    else:
        output.write(ready)


def _write_role_table(title: str, rows: list[list[str]]) -> None:
    from mercury.terminal.theme import body_label

    if not rows:
        return
    output.write(body_label(title, indent=0))
    table = Table.from_headers(
        ["DATABASE", "BACKUP", "ARTIFACT", "RC", "LAST BACKUP"],
        rows,
        style=TableStyle(indent=0),
        min_col_widths=[28, 8, 10, 8, 18],
        max_col_widths=[36, 10, 12, 10, 28],
    )
    display_screen.write_structured_table(table)


def _render_dashboard(dashboard: RecoveryDashboard, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(DASHBOARD_TITLE)

    # Focus first — DEFCON glance target.
    _write_focus(dashboard)
    display_screen.write_blank()

    fields = {
        "Readiness": dashboard.readiness,
        "Scope": (
            f"{dashboard.scope_summary} · {dashboard.development_summary}"
            if dashboard.development_summary
            else dashboard.scope_summary
        ),
        "Latest": dashboard.latest_backup_label,
        "Package": dashboard.package_line,
    }
    if dashboard.temp_restore_schemas:
        fields["Temp"] = f"{len(dashboard.temp_restore_schemas)} _restorecheck_*"
    _write_header_fields(fields)
    display_screen.write_blank()

    prod_rows = [
        [row.database, row.freshness, row.artifact, row.restore_check, row.last_backup]
        for row in dashboard.rows
        if row.role == "prod"
    ]
    _write_role_table("Production", prod_rows)
    display_screen.write_blank()

    options: list[tuple[str, str]] = []
    if dashboard.runnable_pending:
        count = len(dashboard.runnable_pending)
        run_pending_label = f"Run pending restore-checks ({count})      recommended"
    else:
        run_pending_label = "Run pending restore-checks"
    options.append(("1", run_pending_label))
    options.append(("2", "Selected production restore-checks"))
    if dashboard.temp_restore_schemas:
        options.append(
            (
                "3",
                f"Clean up restore-check DBs ({len(dashboard.temp_restore_schemas)})",
            )
        )
    options.append(("4", "Pinned / destination recovery"))
    options.append(("5", "Receiving guide (Main [7])"))
    options.append(("6", "Receipts / verification history"))
    render_submenu(options, indent=0)


def _dump_size_bytes(plan: RestoreCheckPlan) -> int | None:
    if not plan.dump_file or not plan.backup_directory:
        return None
    path = Path(plan.backup_directory) / plan.dump_file
    try:
        return path.stat().st_size
    except OSError:
        return None


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

    from mercury.terminal.format import format_bytes

    # Smallest first — quick wins before multi-minute Scytale-sized imports.
    plans = sorted(
        plans,
        key=lambda plan: (_dump_size_bytes(plan) is None, _dump_size_bytes(plan) or 0),
    )
    large_threshold = 50 * 1024 * 1024
    size_bits: list[str] = []
    any_large = False
    for plan in plans:
        size = _dump_size_bytes(plan)
        if size is None:
            size_bits.append(f"{plan.source_prod}=?")
            continue
        if size >= large_threshold:
            any_large = True
            size_bits.append(f"{plan.source_prod}={format_bytes(size)}*")
        else:
            size_bits.append(f"{plan.source_prod}={format_bytes(size)}")
    display_screen.write_summary("Sizes  " + " · ".join(size_bits))
    if any_large:
        display_screen.write_summary(
            "* large — several minutes; leave this screen running"
        )

    names = ", ".join(plan.source_prod for plan in plans)
    if not menu_prompts.ask_yes_no(
        f"Run restore-check for {len(plans)} database(s) ({names})?",
        default=False,
    ):
        display_screen.write_summary("Restore-check cancelled.")
        return

    passed = 0
    failed = 0
    skipped = 0
    total = len(plans)
    for index, plan in enumerate(plans, start=1):
        size = _dump_size_bytes(plan)
        size_bit = f" · {format_bytes(size)}" if size is not None else ""
        display_screen.write_summary(
            f"[{index}/{total}] {plan.source_prod}{size_bit}"
        )
        if not plan.dump_file or not plan.backup_directory:
            display_screen.write_status(
                "warn",
                f"{plan.source_prod}: missing dump path — skipped.",
            )
            skipped += 1
            continue
        dump_path = Path(plan.backup_directory) / plan.dump_file
        last_beat = [0.0]

        def _on_progress(
            uncompressed: int,
            _compressed: int,
            elapsed: float,
            *,
            _name: str = plan.source_prod,
        ) -> None:
            # Time-gated heartbeats — avoid a line every 16 MiB on huge dumps.
            if elapsed - last_beat[0] < 20 and uncompressed > 0:
                return
            last_beat[0] = elapsed
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            display_screen.write_summary(
                f"  …{_name}: {format_bytes(uncompressed)} ({mins}m{secs:02d}s)"
            )

        try:
            result = execute_restore_into_database(
                target_database=plan.restore_target,
                dump_path=dump_path,
                source_database=plan.source_prod,
                execute=True,
                policy=policy,
                recreate_target=True,
                cleanup_after_success=True,
                on_import_progress=_on_progress,
            )
        except Exception as exc:  # noqa: BLE001 — keep batch going under DEFCON
            display_screen.write_status(
                "fail",
                f"{plan.source_prod}: {exc}",
            )
            failed += 1
            continue
        print_restore_execution_result(result, compact=True)
        if result.executed and result.verification_passed is not False and not result.refused:
            passed += 1
        else:
            failed += 1

    display_screen.write_blank()
    if failed or skipped:
        display_screen.write_status(
            "warn" if passed else "fail",
            f"Batch complete: {passed} passed, {failed} failed, {skipped} skipped "
            f"(of {total}).",
        )
    else:
        display_screen.write_status(
            "ok",
            f"Batch complete: {passed}/{total} restore-checks passed.",
        )


def _run_selected(dashboard: RecoveryDashboard) -> None:
    """Numbered production picker — clearer than free-text names under DEFCON."""
    prod_rows = [row for row in dashboard.rows if row.role == "prod"]
    if not prod_rows:
        display_screen.write_status("warn", "No production databases in recovery scope.")
        return
    display_screen.write_summary("Production databases:")
    for index, row in enumerate(prod_rows, start=1):
        marker = " *" if row.pending else ""
        display_screen.write_summary(
            f"  [{index}] {row.database}  {row.restore_check}{marker}"
        )
    display_screen.write_summary("* = restore-check pending")
    raw = menu_prompts.ask_stripped(
        "Numbers (space/comma-separated), or Enter to cancel: "
    )
    if not raw:
        display_screen.write_summary("Selection cancelled.")
        return
    tokens = [part.strip() for part in raw.replace(",", " ").split() if part.strip()]
    names: list[str] = []
    for token in tokens:
        if not token.isdigit():
            display_screen.write_status("warn", f"Invalid selection: {token}")
            return
        index = int(token)
        if index < 1 or index > len(prod_rows):
            display_screen.write_status("warn", f"Out of range: {token}")
            return
        names.append(prod_rows[index - 1].database)
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
            if not dashboard.temp_restore_schemas:
                output.write(menu_prompts.invalid_choice_message(choice))
                continue
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
