"""Interactive production backup menu (option 1)."""

from __future__ import annotations

import shutil
from collections import Counter
from datetime import datetime, timezone

from mercury import output
from mercury.core.execution_policy import backup_root_state_is_ready
from mercury.menu import main_display as menu_display
from mercury.menu import prompts as menu_prompts
from mercury.terminal import screen as display_screen
from mercury.backup.batch_runner import (
    apply_full_backup_run_evidence,
    build_full_backup_global_refusal_result,
    build_full_backup_run_result,
    new_full_backup_run_id,
    run_backup_batch,
    verify_written_backup_batch,
    write_full_backup_run_receipt,
    write_host_local_refusal_record,
)
from mercury.backup import (
    BackupStatusEntry,
    build_backup_status_report,
    build_database_bundle_plan,
    write_database_bundle_plan,
)
from mercury.backup.freshness import (
    backup_entry_freshness_label,
    backup_entry_status_label,
    backup_entry_verify_label,
    menu_handoff_problem_summary,
)
from mercury.backup.menu_options import (
    backup_menu_render_options,
)
from mercury.backup.terminal.batch import (
    print_backup_batch_result,
    print_batch_small_backup_warnings,
    print_full_backup_run_result,
    print_global_backup_refusal,
)
from mercury.backup.write_preflight import assess_backup_write_preflight
from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed
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
from mercury.menu.subscreen import pause_and_redraw, render_submenu
from mercury.restore.interactive_menu import run_restore_menu

BACKUP_SCREEN_TITLE = "Backup Operations"


def _backup_target_label(policy) -> str:
    state = policy.backup_root_state()
    if backup_root_state_is_ready(state):
        return "operator storage mounted"
    if state == "usb not mounted":
        return "operator storage not mounted"
    if state == "repo-local fallback":
        return "repo-local fallback"
    if state == "low free space":
        return "operator storage mounted; low free space"
    return state.replace("-", " ")


def read_backup_choice() -> str | None:
    # Leading blank matches the section gap after [0] Back.
    while True:
        choice = menu_prompts.ask_stripped("\nChoice: ")
        if choice is None or choice == "0":
            return choice
        if choice:
            return choice
        output.write(menu_prompts.invalid_choice_message(choice))


def _write_backup_fields(fields: dict[str, str]) -> None:
    """Write aligned storage-summary fields (label column padded for readability)."""
    if not fields:
        return
    label_width = max(len(name) for name in fields) + 1  # include colon
    for name, value in fields.items():
        label = f"{name}:"
        output.write(f"  {label:<{label_width}}  {value}")


def _write_phase3b_note(warning: str) -> None:
    from mercury.terminal.theme import hint_text

    for part in warning.splitlines():
        text = part.strip()
        if text:
            output.write(hint_text(text))


def _storage_usage_fields(policy) -> dict[str, str]:
    from mercury.core.environment_status import discover_usb_target

    usb = discover_usb_target()
    root = policy.backup_root.resolve()
    state = policy.backup_root_state()
    host = load_host_maintenance()
    hdd_writes = writes_allowed(host)

    if not hdd_writes:
        storage_label = "mounted" if root.exists() else "not mounted"
        if backup_root_state_is_ready(state) and root.exists():
            storage_label = "mounted and validated"
        elif state == "usb not mounted":
            storage_label = "operator storage not mounted"
        fields: dict[str, str] = {
            "Backup root": str(root),
            "Storage": storage_label,
            "Write state": "disabled · HDD detach maintenance",
            "Active writer": host.active_write_role or "none",
            "Backup actions": "unavailable",
        }
        if root.exists():
            try:
                usage = shutil.disk_usage(root)
                fields["Free"] = format_bytes(usage.free)
            except OSError:
                fields["Free"] = "unknown"
        else:
            fields["Free"] = "n/a"
        return fields

    fields = {
        "Backup root": str(root),
        "Environment": _backup_target_label(policy),
        "Write state": "enabled",
        "Active writer": host.active_write_role or "primary",
        "Backup actions": "available",
    }
    if not usb.mounted:
        try:
            from mercury.core.storage_roots import load_storage_config
            from mercury.core.storage_roles import StorageWriteRole

            storage = load_storage_config(warn_deprecated=False)
            if storage.cutover_complete and storage.active_write_role == StorageWriteRole.PRIMARY:
                # USB archive is optional after cutover — only hint when backup root itself is down.
                if state in {"usb not mounted", "missing path"}:
                    fields["Mount fix"] = "./run.sh storage validate"
            elif usb.quick_mount_command:
                from mercury.repair.usb import USB_REPAIR_COMMAND

                fields["Mount fix"] = USB_REPAIR_COMMAND
        except Exception:
            if usb.quick_mount_command:
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
    elif backup_root_state_is_ready(state):
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
    """Combined label retained for recovery/handoff helpers."""
    return backup_entry_status_label(entry)


def _freshness_label(entry) -> str:
    return backup_entry_freshness_label(entry)


def _verify_label(entry) -> str:
    return backup_entry_verify_label(entry)


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

    def append_row(name: str) -> None:
        entry = status_entries.get(name)
        record = latest_records.get(name)
        rows.append(
            [
                name,
                _freshness_label(entry),
                _verify_label(entry),
                format_bytes(record.size_bytes) if record and record.size_bytes is not None else "-",
                _format_last_backup(
                    record.created_at if record else None,
                    entry.backup_age if entry else None,
                ),
            ]
        )

    for name in sorted(plan.backup_sources):
        classification = classify_database(name)
        if classification.role == DatabaseRole.SHARED_AUTHORITY:
            append_row(name)

    for pair in pairs:
        append_row(pair.prod)

    extra_prod_sources = sorted(
        name
        for name in plan.backup_sources
        if classify_database(name).role == DatabaseRole.PRODUCTION and name not in paired_prod_names
    )
    for name in extra_prod_sources:
        append_row(name)

    return rows


def _status_counts(rows: list[list[str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        if len(row) >= 3:
            counts[row[1]] += 1  # freshness
            counts[row[2]] += 1  # verify
    return counts


def _load_plan() -> BackupPlanDryRun:
    live = should_probe_database_status()
    inventory = discover_for_planning(live=live)
    return build_backup_plan_from_inventory(inventory, live=live)


def _render_backup_screen(plan: BackupPlanDryRun, *, show_title: bool) -> None:
    if show_title:
        menu_display.open_screen(BACKUP_SCREEN_TITLE)
    policy = load_execution_policy()
    _write_backup_fields(_storage_usage_fields(policy))
    display_screen.write_blank()
    status_report = build_backup_status_report(live=should_probe_database_status())
    status_entries = {entry.database: entry for entry in status_report.entries}
    rows = _backup_screen_rows(plan, status_entries=status_entries)

    body_notes: list[tuple[str, str]] = []  # ("warn"|"info"|"hint"|"summary", text)
    if rows:
        table = Table.from_headers(
            ["DATABASE", "FRESHNESS", "VERIFY", "SIZE", "LAST BACKUP"],
            rows,
            style=TableStyle(indent=0),
            min_col_widths=[28, 10, 14, 10, 28],
            max_col_widths=[36, 12, 22, 12, 44],
        )
        display_screen.write_structured_table(table)
        counts = _status_counts(rows)
        problem_parts: list[str] = []
        for label in (
            "Stale",
            "Unknown",
            "Empty",
            "Missing",
            "Unverified",
            "Verify failed",
            "Missing manifest",
            "Absent",
            "Restore-check failed",
            "RC passed · unstamped",
            "OK unstamped",
            "OK* · no RC",
            "Not restore-checked",
        ):
            count = counts.get(label, 0)
            if count:
                if label == "Absent":
                    problem_parts.append(f"{count} absent from server")
                elif label == "OK* · no RC":
                    problem_parts.append(f"{count} OK* · no RC")
                elif label == "RC passed · unstamped":
                    problem_parts.append(f"{count} RC passed · unstamped")
                else:
                    problem_parts.append(f"{count} {label.lower()}")
        if problem_parts:
            only_absent = all(part.endswith("absent from server") for part in problem_parts)
            message = (
                "Catalog source(s) not on this MariaDB server: "
                + ", ".join(problem_parts)
                + "."
                if only_absent
                else menu_handoff_problem_summary(problem_parts)
            )
            body_notes.append(("info" if only_absent else "warn", message))
        else:
            body_notes.append(
                (
                    "summary",
                    "All visible production backups are verified and fresh "
                    "(freshness and integrity are separate checks).",
                )
            )
    else:
        display_screen.write_status("warn", "No databases in active backup scope.")

    for warning in getattr(status_report, "warnings", []) or []:
        # Phase 3B separation is informational, not a repair-style warning.
        if "Phase 3B" in warning:
            body_notes.append(("hint", warning))
        else:
            body_notes.append(("status_warn", warning))

    if body_notes:
        display_screen.write_blank()
        for kind, text in body_notes:
            if kind == "warn":
                output.write(f"[WARN] {text}")
            elif kind == "info":
                output.write(f"[INFO] {text}")
            elif kind == "hint":
                _write_phase3b_note(text)
            elif kind == "summary":
                display_screen.write_summary(text)
            else:
                display_screen.write_status("warn", text)

    # One blank line before the numbered menu (after table and any notes).
    display_screen.write_blank()
    render_submenu(
        backup_menu_render_options(
            writes_allowed=writes_allowed(),
        ),
        indent=0,
    )


def _preview_backup_plan(plan: BackupPlanDryRun) -> None:
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=False,
        live=should_probe_database_status(),
        sources=list(plan.backup_sources),
    )
    print_backup_batch_result(
        batch,
        compact=True,
        menu=True,
        databases_label="Production databases selected",
        suggest_verify=False,
    )


def _run_backup(plan: BackupPlanDryRun) -> None:
    """Production-only backup workflow (menu [3])."""
    preflight = assess_backup_write_preflight()
    if not preflight.allowed:
        print_global_backup_refusal(
            reason="Mercury is in HDD detach maintenance mode",
            detail_lines=preflight.detail_lines,
            next_steps=preflight.next_steps,
        )
        return
    policy = load_execution_policy()
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=True,
        live=should_probe_database_status(),
        policy=policy,
        sources=list(plan.backup_sources),
    )
    print_backup_batch_result(
        batch,
        compact=True,
        menu=True,
        databases_label="Production databases selected",
        suggest_verify=True,
    )
    print_batch_small_backup_warnings(batch)


def _run_development_backup(*, require_confirmation: bool = True):
    """Development-only optional recovery backup (menu [9])."""
    from mercury.backup.batch_runner import resolve_development_backup_sources

    preflight = assess_backup_write_preflight()
    if not preflight.allowed:
        print_global_backup_refusal(
            reason="Mercury is in HDD detach maintenance mode",
            detail_lines=preflight.detail_lines,
            next_steps=preflight.next_steps,
        )
        return None

    sources = resolve_development_backup_sources(live=should_probe_database_status())
    if not sources:
        display_screen.write_summary(
            "No configured development databases are present on this MariaDB server."
        )
        return None
    display_screen.open_screen("Development Database Backup")
    display_screen.write_fields(
        {
            "Development databases selected": str(len(sources)),
            "Databases": ", ".join(sources),
            "Purpose": "Optional pre-migration recovery capture",
        }
    )
    display_screen.write_blank()
    display_screen.write_summary(
        "This is not part of routine production protection or the default handoff package."
    )
    if require_confirmation and not menu_prompts.ask_confirmation_phrase(
        "BACKUP DEV DATABASES", action="back up development databases"
    ):
        display_screen.write_summary("Development backup cancelled.")
        return None
    batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=True,
        live=should_probe_database_status(),
        policy=load_execution_policy(),
        sources=sources,
        allow_development_backup=True,
    )
    print_backup_batch_result(
        batch,
        compact=True,
        menu=True,
        databases_label="Development databases selected",
        suggest_verify=False,
    )
    verification = None
    if batch.executed_count:
        verification = verify_written_backup_batch(batch, allow_development_backup=True)
        display_screen.write_summary(
            "Development backups were created and verified for optional migration recovery. "
            "They are not included in the default production handoff bundle unless explicitly selected."
            if verification.failed == 0
            else f"Development backup verification: {verification.verified} verified, {verification.failed} failed."
        )
        for issue in verification.issues:
            display_screen.write_status("fail", issue)
    return batch, verification


def _run_full_backup(plan: BackupPlanDryRun):
    """Full backup: production write+verify, optional development write+verify."""
    preflight = assess_backup_write_preflight()
    if not preflight.allowed:
        started = datetime.now(timezone.utc)
        run_id = new_full_backup_run_id(now=started)
        result = build_full_backup_global_refusal_result(
            run_id=run_id,
            started_at_utc=started.isoformat(),
            reason=preflight.reason,
        )
        print_global_backup_refusal(
            reason="Mercury is in HDD detach maintenance mode",
            detail_lines=preflight.detail_lines,
            next_steps=preflight.next_steps,
        )
        try:
            audit = write_host_local_refusal_record(result)
            result = apply_full_backup_run_evidence(result, receipt_path=audit)
        except Exception:
            result = apply_full_backup_run_evidence(
                result, receipt_path=None, receipt_error="host-local refusal audit not written"
            )
        print_full_backup_run_result(result)
        return result

    include_dev = menu_prompts.ask_yes_no(
        "Also back up configured development databases for migration recovery?",
        default=False,
    ) is True
    started = datetime.now(timezone.utc)
    run_id = new_full_backup_run_id(now=started)
    policy = load_execution_policy()

    production_batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=True,
        live=should_probe_database_status(),
        policy=policy,
        sources=list(plan.backup_sources),
    )
    display_screen.write_summary("Production")
    print_backup_batch_result(
        production_batch,
        compact=True,
        menu=True,
        databases_label="Production databases selected",
        suggest_verify=False,
    )

    production_verification = None
    if production_batch.executed_count:
        production_verification = verify_written_backup_batch(production_batch)
        display_screen.write_summary(
            f"Production verification: {production_verification.verified} verified, "
            f"{production_verification.failed} failed."
        )
        for issue in production_verification.issues:
            display_screen.write_status("fail", issue)

    development_batch = None
    development_verification = None
    if include_dev:
        sources = None
        from mercury.backup.batch_runner import resolve_development_backup_sources

        sources = resolve_development_backup_sources(live=should_probe_database_status())
        if not sources:
            display_screen.write_summary(
                "No configured development databases are present on this MariaDB server."
            )
        else:
            display_screen.write_summary("Development recovery")
            development_batch = run_backup_batch(
                BACKUP_KIND_FULL,
                execute=True,
                live=should_probe_database_status(),
                policy=policy,
                sources=sources,
                allow_development_backup=True,
            )
            print_backup_batch_result(
                development_batch,
                compact=True,
                menu=True,
                databases_label="Development databases selected",
                suggest_verify=False,
            )
            if development_batch.executed_count:
                development_verification = verify_written_backup_batch(
                    development_batch, allow_development_backup=True
                )
                display_screen.write_summary(
                    f"Development verification: {development_verification.verified} verified, "
                    f"{development_verification.failed} failed."
                )
                for issue in development_verification.issues:
                    display_screen.write_status("fail", issue)

    result = build_full_backup_run_result(
        run_id=run_id,
        started_at_utc=started.isoformat(),
        production_batch=production_batch,
        production_verification=production_verification,
        development_batch=development_batch,
        development_verification=development_verification,
        development_requested=include_dev,
    )
    try:
        receipt = write_full_backup_run_receipt(result)
        result = apply_full_backup_run_evidence(result, receipt_path=receipt)
    except Exception as exc:  # noqa: BLE001 — classify evidence failure; never silently PASS
        display_screen.write_status("fail", f"Could not write full-backup run receipt: {exc}")
        result = apply_full_backup_run_evidence(
            result, receipt_path=None, receipt_error=str(exc)
        )

    print_full_backup_run_result(result)
    return result


def _run_verify_sources() -> None:
    preflight = assess_backup_write_preflight()
    if not preflight.allowed:
        # Manifest stamping writes under the HDD — refuse in detach mode.
        print_global_backup_refusal(
            reason=(
                "Verify with manifest stamping refused. "
                f"{preflight.reason}"
            ),
            detail_lines=preflight.detail_lines,
            next_steps=preflight.next_steps,
        )
        return
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

    preflight = assess_backup_write_preflight()
    if not preflight.allowed:
        print_global_backup_refusal(
            reason="Mercury is in HDD detach maintenance mode",
            detail_lines=preflight.detail_lines,
            next_steps=preflight.next_steps,
        )
        return

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
            display_screen.write_summary(
                f"Plan refreshed — {len(plan.backup_sources)} production database(s)."
            )
            show_title = pause_and_redraw()
            continue

        if choice == "2":
            _run_full_backup(plan)
            plan = _load_plan()
            show_title = pause_and_redraw()
            continue

        if choice == "3":
            _run_backup(plan)
            plan = _load_plan()
            show_title = pause_and_redraw()
            continue

        if choice == "4":
            _run_verify_sources()
            show_title = pause_and_redraw()
            continue

        if choice == "5":
            preflight = assess_backup_write_preflight()
            if not preflight.allowed:
                print_global_backup_refusal(
                    reason=(
                        "Restore-check refused. "
                        f"{preflight.reason}"
                    ),
                    detail_lines=preflight.detail_lines,
                    next_steps=preflight.next_steps,
                )
            else:
                run_restore_menu()
            show_title = pause_and_redraw()
            continue

        if choice == "6":
            _write_backup_bundle()
            show_title = pause_and_redraw()
            continue

        if choice == "7":
            _preview_backup_plan(plan)
            show_title = pause_and_redraw()
            continue

        if choice == "8":
            from mercury.handoff.interactive_menu import run_handoff_menu

            run_handoff_menu(interactive=True)
            plan = _load_plan()
            show_title = pause_and_redraw()
            continue

        if choice == "9":
            _run_development_backup()
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
