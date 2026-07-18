"""Interactive production backup menu (option 1)."""

from __future__ import annotations

import shutil
from collections import Counter

from mercury import output
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import run_backup_batch
from mercury.backup import (
    BackupStatusEntry,
    build_backup_status_report,
    build_database_bundle_plan,
    write_database_bundle_plan,
)
from mercury.backup.freshness import backup_entry_status_label, menu_handoff_problem_summary
from mercury.backup.terminal.batch import print_backup_batch_result
from mercury.backup.terminal.bundle import print_database_bundle_plan
from mercury.backup.terminal.verify import print_verify_menu_summary, run_verify_all_for_menu
from mercury.backup.on_disk_index import build_on_disk_backup_list, latest_records_by_database
from mercury.core.execution_policy import load_execution_policy
from mercury.core.runtime import should_probe_database_status
from mercury.core.safety import BACKUP_KIND_FULL
from mercury.terminal.format import format_bytes, format_human_datetime
from mercury.terminal.table import Table, TableStyle
from mercury.database.backup_planning import BackupPlanDryRun, build_backup_plan_from_inventory
from mercury.database.discovery import discover_for_planning
from mercury.database.core.classifier import DatabaseRole, classify_database
from mercury.database.prod_dev_pairs import build_prod_dev_pairs
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.restore.interactive_menu import run_restore_menu

BACKUP_SCREEN_TITLE = "Backup Operations"


def _backup_target_label(policy) -> str:
    state = policy.backup_root_state()
    if state == "usb-mounted":
        return "operator storage mounted"
    if state == "usb not mounted":
        return "operator storage not mounted"
    if state == "repo-local fallback":
        return "repo-local fallback"
    if state == "low free space":
        return "operator storage mounted; low free space"
    return state.replace("-", " ")


def read_backup_choice() -> str | None:
    return read_submenu_choice()


def _storage_usage_fields(policy) -> dict[str, str]:
    from mercury.core.environment_status import discover_usb_target

    usb = discover_usb_target()
    root = policy.backup_root.resolve()
    state = policy.backup_root_state()
    fields: dict[str, str] = {
        "Backup root": str(root),
        "Environment": _backup_target_label(policy),
    }
    if usb.quick_mount_command and not usb.mounted:
        from mercury.repair.usb import USB_REPAIR_COMMAND

        fields["Mount fix"] = USB_REPAIR_COMMAND

    if not root.exists():
        fields["Used"] = "n/a"
        fields["Total"] = "n/a"
        fields["Free"] = "n/a"
        fields["Usage"] = "n/a"
        if state == "missing path":
            fields["Status"] = "path missing — mount operator storage first"
        elif state == "usb not mounted":
            fields["Status"] = "operator storage not mounted"
        else:
            fields["Status"] = state.replace("-", " ")
        return fields

    try:
        usage = shutil.disk_usage(root)
    except OSError:
        fields["Used"] = "unknown"
        fields["Total"] = "unknown"
        fields["Free"] = "unknown"
        fields["Usage"] = "unknown"
        fields["Status"] = "unavailable"
        return fields

    used_percent = 0.0 if usage.total == 0 else (usage.used / usage.total) * 100.0
    if used_percent >= 95.0:
        status = "critical"
    elif used_percent >= 85.0:
        status = "warning"
    elif state == "usb-mounted":
        status = "ok"
    else:
        status = state.replace("-", " ")

    fields["Used"] = format_bytes(usage.used)
    fields["Total"] = format_bytes(usage.total)
    fields["Free"] = format_bytes(usage.free)
    fields["Usage"] = f"{used_percent:.0f}%"
    fields["Status"] = status
    return fields


def _format_last_backup(created_at: str | None, backup_age: str | None = None) -> str:
    _ = backup_age
    return format_human_datetime(created_at)


def _status_label(entry) -> str:
    return backup_entry_status_label(entry)


def _backup_screen_rows(
    plan: BackupPlanDryRun,
    *,
    status_entries: dict[str, BackupStatusEntry] | None = None,
) -> list[list[str]]:
    in_scope_names = [entry.name for entry in plan.classifications]
    pairs = build_prod_dev_pairs(in_scope_names)
    paired_prod_names = {pair.prod for pair in pairs}
    latest_records = {
        record.database: record
        for record in latest_records_by_database(
            build_on_disk_backup_list(load_execution_policy().backup_root)
        )
    }
    if status_entries is None:
        status_entries = {
            entry.database: entry
            for entry in build_backup_status_report(live=should_probe_database_status()).entries
        }

    rows: list[list[str]] = []

    for name in sorted(plan.backup_sources):
        classification = classify_database(name)
        if classification.role == DatabaseRole.SHARED_AUTHORITY:
            entry = status_entries.get(name)
            record = latest_records.get(name)
            rows.append(
                [
                    name,
                    _status_label(entry),
                    format_bytes(record.size_bytes) if record and record.size_bytes is not None else "-",
                    _format_last_backup(
                        record.created_at if record else None,
                        entry.backup_age if entry else None,
                    ),
                ]
            )

    for pair in pairs:
        entry = status_entries.get(pair.prod)
        record = latest_records.get(pair.prod)
        rows.append(
            [
                pair.prod,
                _status_label(entry),
                format_bytes(record.size_bytes) if record and record.size_bytes is not None else "-",
                _format_last_backup(
                    record.created_at if record else None,
                    entry.backup_age if entry else None,
                ),
            ]
        )

    extra_prod_sources = sorted(
        name
        for name in plan.backup_sources
        if classify_database(name).role == DatabaseRole.PRODUCTION and name not in paired_prod_names
    )
    for name in extra_prod_sources:
        entry = status_entries.get(name)
        record = latest_records.get(name)
        rows.append(
            [
                name,
                _status_label(entry),
                format_bytes(record.size_bytes) if record and record.size_bytes is not None else "-",
                _format_last_backup(
                    record.created_at if record else None,
                    entry.backup_age if entry else None,
                ),
            ]
        )

    return rows


def _status_counts(rows: list[list[str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        if len(row) >= 2:
            counts[row[1]] += 1
    return counts


def _load_plan() -> BackupPlanDryRun:
    live = should_probe_database_status()
    inventory = discover_for_planning(live=live)
    return build_backup_plan_from_inventory(inventory, live=live)


def _render_backup_screen(plan: BackupPlanDryRun, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(BACKUP_SCREEN_TITLE)
    policy = load_execution_policy()
    backup_ready = policy.backup_execution_allowed()
    display_screen.write_fields(
        _storage_usage_fields(policy)
    )
    display_screen.write_blank()
    status_report = build_backup_status_report(live=should_probe_database_status())
    status_entries = {entry.database: entry for entry in status_report.entries}
    rows = _backup_screen_rows(plan, status_entries=status_entries)
    if rows:
        table = Table.from_headers(
            ["DATABASE", "STATUS", "SIZE", "LAST BACKUP"],
            rows,
            style=TableStyle(indent=0),
            min_col_widths=[30, 10, 10, 30],
            max_col_widths=[36, 12, 12, 48],
        )
        display_screen.write_structured_table(table)
        display_screen.write_blank()
        counts = _status_counts(rows)
        problem_parts: list[str] = []
        for label in ("Stale", "Unknown", "Missing", "Unverified", "Warning", "Absent"):
            count = counts.get(label, 0)
            if count:
                if label == "Absent":
                    problem_parts.append(f"{count} absent from server")
                else:
                    problem_parts.append(f"{count} {label.lower()}")
        if problem_parts:
            # Absent-only is informational; mix with real problems stays a warn.
            only_absent = all(part.endswith("absent from server") for part in problem_parts)
            display_screen.write_status(
                "info" if only_absent else "warn",
                (
                    "Catalog source(s) not on this MariaDB server: "
                    + ", ".join(problem_parts)
                    + "."
                    if only_absent
                    else menu_handoff_problem_summary(problem_parts)
                ),
            )
        else:
            display_screen.write_summary("All visible source backups are artifact-verified and fresh.")
    else:
        display_screen.write_status("warn", "No databases in active backup scope.")
    options: list[tuple[str, str]] = [
        ("1", "Refresh"),
        ("3", "Verify source backups"),
        ("4", "Restore-check source backups"),
        ("5", "Write DB bundle and runbooks"),
        ("6", "Preview backup plan"),
    ]
    if plan.backup_sources:
        if backup_ready:
            run_label = "Run full backup now"
        else:
            run_label = "Run full backup now (storage/config not ready)"
        options.insert(1, ("2", run_label))
    from mercury.repair.startup import usb_repair_needed

    if usb_repair_needed():
        options.append(("7", "Repair USB mount and permissions"))
    options.append(("8", "Open workstation handoff (main menu 9)"))
    display_screen.write_blank()
    render_submenu(options, indent=0)


def _preview_backup_plan(plan: BackupPlanDryRun) -> None:
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=False,
        live=should_probe_database_status(),
        sources=list(plan.backup_sources),
    )
    print_backup_batch_result(batch, compact=True, menu=True)


def _run_backup(plan: BackupPlanDryRun) -> None:
    policy = load_execution_policy()
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=True,
        live=should_probe_database_status(),
        policy=policy,
        sources=list(plan.backup_sources),
    )
    print_backup_batch_result(batch, compact=True, menu=True)
    if batch.executed_count:
        display_screen.write_summary(
            "Next: use [3] Verify source backups to set manifest verified flags on operator storage."
        )


def _run_verify_sources() -> None:
    summary = run_verify_all_for_menu(update_manifest=True)
    print_verify_menu_summary(summary)
    display_screen.write_blank()
    display_screen.write_summary(
        f"Verification complete — {summary.verified} verified, "
        f"{summary.missing} missing, {summary.failed} failed."
    )


def _write_backup_bundle() -> None:
    from mercury.backup.bundle import bundle_package_status
    from mercury.core.handoff_status import handoff_write_ack_prompt, handoff_write_requires_force
    from mercury.menu.prompts import ask_yes_no

    plan = build_database_bundle_plan(live=should_probe_database_status())
    print_database_bundle_plan(plan, executed=False)
    package_status = bundle_package_status(plan)
    prompt = handoff_write_ack_prompt(package_status)
    default_yes = not handoff_write_requires_force(package_status)
    if ask_yes_no(prompt, default=default_yes) is not True:
        display_screen.write_summary("Bundle write cancelled.")
        return
    try:
        write_database_bundle_plan(plan)
    except ValueError as exc:
        display_screen.write_status("fail", str(exc))
        return
    print_database_bundle_plan(plan, executed=True)


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

        if choice == "3":
            _run_verify_sources()
            plan = _load_plan()
            show_title = pause_and_redraw()
            continue

        if choice == "4":
            run_restore_menu()
            show_title = pause_and_redraw()
            continue

        if choice == "5":
            _write_backup_bundle()
            show_title = pause_and_redraw()
            continue

        if choice == "6":
            _preview_backup_plan(plan)
            show_title = pause_and_redraw()
            continue

        if choice == "7":
            from mercury.repair.startup import run_usb_repair_flow

            run_usb_repair_flow(interactive=True, default_yes=True)
            plan = _load_plan()
            show_title = pause_and_redraw()
            continue

        if choice == "8":
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu(interactive=True)
            plan = _load_plan()
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
