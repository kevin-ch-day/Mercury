"""Interactive restore-check menu inside the backup operations lane."""

from __future__ import annotations

from pathlib import Path

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import resolve_batch_sources
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.restore.check_plan import RestoreCheckPlan, build_restore_check_plan
from mercury.restore.check_cleanup import cleanup_restorecheck_databases, discover_restorecheck_names
from mercury.restore.terminal.check_cleanup import print_restorecheck_cleanup_batch
from mercury.restore.terminal.check import print_restore_check_plans
from mercury.restore.restore_runner import execute_restore_into_database
from mercury.restore.terminal.runner import print_restore_execution_result

RESTORE_SCREEN_TITLE = "Restore-check operations"


def read_restore_choice() -> str | None:
    return read_submenu_choice()


def _load_plans() -> list[RestoreCheckPlan]:
    sources = resolve_batch_sources(live=should_probe_database_status())
    return [build_restore_check_plan(prod) for prod in sources]


def _allowed_plans(plans: list[RestoreCheckPlan]) -> list[RestoreCheckPlan]:
    return [plan for plan in plans if plan.allowed]


def _restorecheck_names_on_server() -> list[str]:
    return discover_restorecheck_names()


def _rc_labels_by_database() -> dict[str, str]:
    from mercury.backup.freshness import backup_entry_needs_restore_check
    from mercury.backup.status import build_backup_status_report

    labels: dict[str, str] = {}
    try:
        report = build_backup_status_report(live=should_probe_database_status())
    except Exception:
        return labels
    for entry in report.entries:
        if backup_entry_needs_restore_check(entry):
            status = getattr(entry, "restore_check_status", None)
            labels[entry.database] = (
                "Failed"
                if status in {"failed", "verification_failed"}
                else "None"
            )
        elif getattr(entry, "restore_check_status", None) == "passed":
            labels[entry.database] = "Passed"
        else:
            labels[entry.database] = "None"
    return labels


def _pending_restore_check_names(plans: list[RestoreCheckPlan]) -> list[str]:
    rc_labels = _rc_labels_by_database()
    return [
        plan.source_prod
        for plan in plans
        if plan.allowed and rc_labels.get(plan.source_prod, "None") != "Passed"
    ]


def _write_restore_focus(pending: list[str], *, can_run: bool) -> None:
    from mercury.terminal.theme import (
        active_styles,
        colors_enabled,
        hint_text,
        markup,
        status_badge,
    )

    if not pending:
        ready = "Next: No restore-check gaps for ready sources"
        if colors_enabled():
            output.write(f"{status_badge('ok')} {markup(ready, active_styles().ok)}")
        else:
            output.write(ready)
        return

    next_line = "Next: Run restore-checks [2]"
    if not can_run:
        next_line = f"{next_line} · enable live mode first"
    pending_line = f"Pending: {', '.join(pending)}"
    if colors_enabled():
        styles = active_styles()
        output.write(f"{status_badge('warn')} {markup(next_line, styles.recommended)}")
        output.write(markup(pending_line, styles.value))
    else:
        output.write(next_line)
        output.write(pending_line)
    output.write(
        hint_text("Imports into disposable _restorecheck_* databases only — never *_prod.")
    )


def _render_restore_screen(plans, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(RESTORE_SCREEN_TITLE)
    pending = _pending_restore_check_names(plans)
    policy = load_execution_policy()
    can_run = bool(_allowed_plans(plans)) and policy.live_execution_allowed()
    _write_restore_focus(pending, can_run=can_run if _allowed_plans(plans) else False)
    display_screen.write_blank()
    if not plans:
        menu_display.write_status("warn", "No backup sources found.")
    else:
        print_restore_check_plans(
            plans,
            compact=True,
            menu=True,
            rc_by_database=_rc_labels_by_database(),
        )
    display_screen.write_blank()
    options: list[tuple[str, str]] = [("1", "Refresh")]
    if _allowed_plans(plans):
        label = "Run restore-checks"
        if not policy.live_execution_allowed():
            label = f"{label} (live mode required)"
        if pending:
            label = f"{label}      recommended"
        options.append(("2", label))
    else:
        options.append(("2", "Run restore-checks (none ready)"))
    restorecheck_count = len(_restorecheck_names_on_server())
    if restorecheck_count:
        options.append(("3", f"Clean up temp restore-check databases ({restorecheck_count})"))
    render_submenu(options)


def _run_allowed_restore_checks(plans: list[RestoreCheckPlan]) -> None:
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    allowed = _allowed_plans(plans)
    if not allowed:
        menu_display.write_status("warn", "No allowed restore-check plans.")
        return

    for plan in allowed:
        if not plan.dump_file or not plan.backup_directory:
            continue
        dump_path = Path(plan.backup_directory) / plan.dump_file
        result = execute_restore_into_database(
            target_database=plan.restore_target,
            dump_path=dump_path,
            source_database=plan.source_prod,
            execute=execute,
            policy=policy,
            recreate_target=True,
            cleanup_after_success=True,
        )
        print_restore_execution_result(result, compact=True)


def _cleanup_restorecheck_databases() -> None:
    from mercury.core.execution_policy import load_execution_policy

    names = _restorecheck_names_on_server()
    policy = load_execution_policy()
    execute = policy.live_execution_allowed()
    batch = cleanup_restorecheck_databases(names, execute=execute)
    print_restorecheck_cleanup_batch(batch, compact=True)


def run_restorecheck_cleanup() -> None:
    """Drop leftover ``_restorecheck_*`` databases (policy-gated)."""
    _cleanup_restorecheck_databases()


def run_restore_menu(*, interactive: bool = True) -> None:
    """Compatibility entry — consolidated under the recovery dashboard."""
    from mercury.restore.interactive_dashboard import run_recovery_dashboard

    run_recovery_dashboard(interactive=interactive)
