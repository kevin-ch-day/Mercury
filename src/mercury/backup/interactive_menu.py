"""Interactive production backup menu (option 1)."""

from __future__ import annotations

import shutil
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from types import TracebackType

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
    backup_entry_needs_backup_work,
    backup_entry_needs_restore_check,
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

BACKUP_SCREEN_TITLE = "Backup Operations"
_DUMP_HEARTBEAT_SECONDS = 20.0


class _DumpHeartbeat:
    """Time-gated 'still dumping' lines so multi-minute DBs do not look hung."""

    def __init__(self, label: str, *, interval: float = _DUMP_HEARTBEAT_SECONDS) -> None:
        self._label = label
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"mercury-dump-heartbeat-{label}",
            daemon=True,
        )
        self._started = time.monotonic()

    def __enter__(self) -> _DumpHeartbeat:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            elapsed = time.monotonic() - self._started
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            display_screen.write_summary(
                f"  …{self._label}: still dumping ({mins}m{secs:02d}s)"
            )


def _backup_target_label(policy) -> str:
    state = policy.backup_root_state()
    if backup_root_state_is_ready(state):
        return "operator storage mounted"
    if state == "operator mount not mounted":
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
    """Write aligned storage-summary fields (colon-free operator rows)."""
    if not fields:
        return
    from mercury.terminal.theme import dashboard_row

    label_width = max(len(name) for name in fields) + 2
    for name, value in fields.items():
        output.write(dashboard_row(name, value, label_width=label_width))


def _write_phase3b_note(warning: str) -> None:
    from mercury.terminal.theme import hint_text

    text = " ".join(part.strip() for part in warning.splitlines() if part.strip())
    if text:
        output.write(hint_text(text))


def _write_focus_callout(
    *,
    needs_backup: bool,
    pending_rc: list[str],
) -> None:
    """High-visibility operator focus block (DEFCON glance target)."""
    from mercury.terminal.theme import (
        active_styles,
        colors_enabled,
        hint_text,
        markup,
        status_badge,
    )

    if pending_rc and not needs_backup:
        next_line = (
            f"Next: Restore and disaster recovery [5] ({len(pending_rc)})"
        )
        pending_line = f"Pending: {', '.join(pending_rc)}"
        if colors_enabled():
            styles = active_styles()
            output.write(f"{status_badge('warn')} {markup(next_line, styles.recommended)}")
            output.write(markup(pending_line, styles.value))
        else:
            output.write(next_line)
            output.write(pending_line)
        output.write(
            hint_text("Back [0] → Main Menu [5]. Do not run another backup.")
        )
        return

    if needs_backup:
        next_line = "Next: Guided backup session [1]"
        if colors_enabled():
            styles = active_styles()
            output.write(f"{status_badge('warn')} {markup(next_line, styles.recommended)}")
        else:
            output.write(next_line)
        if pending_rc:
            pending_line = (
                f"Pending: restore-check after backup · {', '.join(pending_rc)}"
            )
            if colors_enabled():
                output.write(markup(pending_line, active_styles().value))
            else:
                output.write(pending_line)
        return

    ready = "Next: Production backups look ready on this screen"
    if colors_enabled():
        output.write(f"{status_badge('ok')} {markup(ready, active_styles().ok)}")
    else:
        output.write(ready)


def _storage_usage_fields(policy) -> dict[str, str]:
    """Compact Backup Operations header fields (root, writer, status+capacity)."""
    root = policy.backup_root.resolve()
    state = policy.backup_root_state()
    host = load_host_maintenance()
    hdd_writes = writes_allowed(host)

    writer = (
        "Enabled"
        if hdd_writes
        else "Disabled · disconnect preparation"
    )
    fields: dict[str, str] = {
        "Backup root": str(root),
        "Backup writer": writer,
    }

    if not root.exists():
        if state == "missing path":
            fields["Status"] = "path missing — mount operator storage first"
        elif state == "operator mount not mounted":
            fields["Status"] = "operator storage not mounted"
        else:
            fields["Status"] = state.replace("-", " ")
        return fields

    try:
        usage = shutil.disk_usage(root)
    except OSError:
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

    # One line: status + capacity (drops a header row on the DEFCON screen).
    fields["Status"] = (
        f"{status} · {format_bytes(usage.used)} used · "
        f"{format_bytes(usage.free)} free ({used_percent:.0f}%)"
    )
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
    """Compact restore-check column for Backup Operations table."""
    label = backup_entry_verify_label(entry)
    return {
        "Restore-check passed": "Passed",
        "Restore-check failed": "Failed",
        "Not restore-checked": "Pending",
        "OK* · no RC": "Pending*",
        "RC passed · unstamped": "Passed*",
        "OK unstamped": "OK*",
    }.get(label, label)


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
    status_report = build_backup_status_report(live=should_probe_database_status())
    status_entries = {entry.database: entry for entry in status_report.entries}
    rows = _backup_screen_rows(plan, status_entries=status_entries)

    body_notes: list[tuple[str, str]] = []  # ("warn"|"info"|"hint"|"summary", text)
    pending_rc = [
        entry.database
        for entry in status_report.entries
        if backup_entry_needs_restore_check(entry)
    ]
    needs_backup = any(
        backup_entry_needs_backup_work(entry) for entry in status_report.entries
    )
    counts = _status_counts(rows) if rows else Counter()
    if any(
        counts.get(label, 0)
        for label in (
            "Stale",
            "Unknown",
            "Empty",
            "Missing",
            "Unverified",
            "Verify failed",
            "Missing manifest",
            "Absent",
            "RC passed · unstamped",
            "OK unstamped",
        )
    ):
        needs_backup = True
    if not pending_rc:
        # Fall back to visible restore-check labels when status entries are sparse.
        for row in rows:
            if len(row) >= 3 and row[2] in {
                "Not restore-checked",
                "OK* · no RC",
                "Restore-check failed",
                "Pending",
                "Pending*",
                "Failed",
            }:
                pending_rc.append(row[0])

    # Focus first (DEFCON glance), then compact storage fields, then table.
    _write_focus_callout(needs_backup=needs_backup, pending_rc=pending_rc)
    display_screen.write_blank()
    _write_backup_fields(_storage_usage_fields(policy))
    display_screen.write_blank()

    if rows:
        table = Table.from_headers(
            ["DATABASE", "FRESHNESS", "RC", "SIZE", "LAST BACKUP"],
            rows,
            style=TableStyle(indent=0),
            min_col_widths=[28, 10, 8, 10, 28],
            max_col_widths=[36, 12, 12, 12, 44],
        )
        display_screen.write_structured_table(table)
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
            "Failed",
            "RC passed · unstamped",
            "Passed*",
            "OK unstamped",
            "OK*",
            "OK* · no RC",
            "Pending*",
            "Not restore-checked",
            "Pending",
        ):
            count = counts.get(label, 0)
            if count:
                if label == "Absent":
                    problem_parts.append(f"{count} absent from server")
                elif label in {"OK* · no RC", "Pending*"}:
                    problem_parts.append(f"{count} OK* · no RC")
                elif label in {"RC passed · unstamped", "Passed*"}:
                    problem_parts.append(f"{count} RC passed · unstamped")
                elif label in {"Not restore-checked", "Pending"}:
                    problem_parts.append(f"{count} not restore-checked")
                elif label == "Failed":
                    problem_parts.append(f"{count} restore-check failed")
                else:
                    problem_parts.append(f"{count} {label.lower()}")
        # Restore-check-only gaps are already covered by the focus callout.
        if problem_parts and not (pending_rc and not needs_backup):
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
            else:
                display_screen.write_status("warn", text)

    # One blank line before the numbered menu (after table and any notes).
    display_screen.write_blank()
    render_submenu(
        backup_menu_render_options(
            writes_allowed=writes_allowed(),
            recommend_guided=needs_backup,
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


def _ensure_writes_then_continue():
    """Return availability when backup writes are ready (after optional guided restore)."""
    from mercury.storage.operation_availability import (
        OperationStatus,
        ensure_backup_writes_available,
    )

    availability = ensure_backup_writes_available(interactive=True)
    if (
        not availability.available
        and availability.operation_status == OperationStatus.CANCELLED
    ):
        display_screen.write_summary("Backup cancelled.")
        display_screen.write_summary("Mercury writes remain disabled.")
    return availability


def _production_protection_complete(plan: BackupPlanDryRun) -> bool:
    """True when planned production sources are fresh and restore-checked."""
    report = build_backup_status_report(live=should_probe_database_status())
    by_name = {entry.database: entry for entry in report.entries}
    sources = list(plan.backup_sources)
    if not sources:
        return False
    for name in sources:
        entry = by_name.get(name)
        if entry is None:
            return False
        if backup_entry_needs_backup_work(entry):
            return False
        if backup_entry_needs_restore_check(entry):
            return False
    return True


def _confirm_redundant_production_backup(plan: BackupPlanDryRun) -> bool:
    """Ask before rewriting fresh restore-checked production backups."""
    if not _production_protection_complete(plan):
        return True
    display_screen.write_status(
        "warn",
        "Production backups are already fresh and restore-checked.",
    )
    display_screen.write_summary(
        "A new full backup creates new IDs and will require restore-check again."
    )
    return bool(
        menu_prompts.ask_yes_no("Run another production backup anyway?", default=False)
    )


def _run_backup(plan: BackupPlanDryRun) -> None:
    """Production-only backup workflow (menu [3])."""
    from mercury.storage.host_maintenance import mark_source_changed_since_package
    from mercury.storage.operation_availability import note_backup_after_transition

    availability = _ensure_writes_then_continue()
    if not availability:
        return
    if not _confirm_redundant_production_backup(plan):
        display_screen.write_summary("Production backup cancelled.")
        return
    policy = load_execution_policy()
    backup_ran = False
    backup_ok: bool | None = None
    sources = list(plan.backup_sources)
    active_hb: list[_DumpHeartbeat | None] = [None]

    def _progress(index: int, total: int, database: str) -> None:
        if active_hb[0] is not None:
            active_hb[0].__exit__(None, None, None)
            active_hb[0] = None
        display_screen.write_summary(f"  {index}/{total}  {database}")
        active_hb[0] = _DumpHeartbeat(database).__enter__()

    try:
        batch = run_backup_batch(
            BACKUP_KIND_FULL,
            execute=True,
            live=should_probe_database_status(),
            policy=policy,
            sources=sources,
            on_database_start=_progress,
        )
        backup_ran = True
        backup_ok = bool(getattr(batch, "executed_count", 0))
        if backup_ok:
            mark_source_changed_since_package()
        print_backup_batch_result(
            batch,
            compact=True,
            menu=True,
            databases_label="Production databases selected",
            suggest_verify=False,
            summary_only=True,
        )
        if batch.executed_count:
            verification = verify_written_backup_batch(batch)
            display_screen.write_summary(
                f"Prod verify · {verification.verified} verified · "
                f"{verification.failed} failed"
            )
            for issue in verification.issues:
                display_screen.write_status("fail", issue)
        print_batch_small_backup_warnings(batch)
    finally:
        if active_hb[0] is not None:
            active_hb[0].__exit__(None, None, None)
            active_hb[0] = None
        note_backup_after_transition(
            availability, backup_ran=backup_ran, backup_succeeded=backup_ok
        )


def _run_development_backup(*, require_confirmation: bool = True):
    """Development backup for required recovery-scope schemas (menu [4])."""
    from mercury.backup.batch_runner import resolve_development_backup_sources
    from mercury.storage.host_maintenance import mark_source_changed_since_package
    from mercury.storage.operation_availability import note_backup_after_transition

    availability = _ensure_writes_then_continue()
    if not availability:
        return None

    sources = resolve_development_backup_sources(live=should_probe_database_status())
    if not sources:
        display_screen.write_summary(
            "No configured development databases are present on this MariaDB server."
        )
        return None
    display_screen.open_screen("Development Database Backup")
    display_screen.write_summary(
        f"Dev recovery scope · {len(sources)} databases · not handoff package"
    )
    if require_confirmation and not menu_prompts.ask_confirmation_phrase(
        "BACKUP DEV DATABASES", action="back up development databases"
    ):
        display_screen.write_summary("Development backup cancelled.")
        return None
    backup_ran = False
    backup_ok: bool | None = None
    active_hb: list[_DumpHeartbeat | None] = [None]

    def _progress(index: int, total: int, database: str) -> None:
        if active_hb[0] is not None:
            active_hb[0].__exit__(None, None, None)
            active_hb[0] = None
        display_screen.write_summary(f"  {index}/{total}  {database}")
        active_hb[0] = _DumpHeartbeat(database).__enter__()

    try:
        batch = run_backup_batch(
            BACKUP_KIND_FULL,
            execute=True,
            live=should_probe_database_status(),
            policy=load_execution_policy(),
            sources=sources,
            allow_development_backup=True,
            on_database_start=_progress,
        )
        backup_ran = True
        backup_ok = bool(getattr(batch, "executed_count", 0))
        if backup_ok:
            mark_source_changed_since_package()
        print_backup_batch_result(
            batch,
            compact=True,
            menu=True,
            databases_label="Development databases selected",
            suggest_verify=False,
            summary_only=True,
        )
        verification = None
        if batch.executed_count:
            verification = verify_written_backup_batch(batch, allow_development_backup=True)
            display_screen.write_summary(
                f"Dev verify · {verification.verified} verified · {verification.failed} failed"
            )
            for issue in verification.issues:
                display_screen.write_status("fail", issue)
        return batch, verification
    finally:
        if active_hb[0] is not None:
            active_hb[0].__exit__(None, None, None)
            active_hb[0] = None
        note_backup_after_transition(
            availability, backup_ran=backup_ran, backup_succeeded=backup_ok
        )


def _run_full_backup(plan: BackupPlanDryRun):
    """Full backup: production write+verify, optional development write+verify."""
    from mercury.storage.host_maintenance import mark_source_changed_since_package
    from mercury.storage.operation_availability import note_backup_after_transition

    availability = _ensure_writes_then_continue()
    if not availability:
        started = datetime.now(timezone.utc)
        run_id = new_full_backup_run_id(now=started)
        result = build_full_backup_global_refusal_result(
            run_id=run_id,
            started_at_utc=started.isoformat(),
            reason="backup writer unavailable or restoration declined",
        )
        try:
            audit = write_host_local_refusal_record(result)
            result = apply_full_backup_run_evidence(result, receipt_path=audit)
        except Exception:
            result = apply_full_backup_run_evidence(
                result, receipt_path=None, receipt_error="host-local refusal audit not written"
            )
        print_full_backup_run_result(result)
        return None

    if not _confirm_redundant_production_backup(plan):
        display_screen.write_summary("Full backup cancelled.")
        return None

    include_dev = menu_prompts.ask_yes_no(
        "Also back up configured development databases for migration recovery?",
        default=False,
    ) is True
    started = datetime.now(timezone.utc)
    run_id = new_full_backup_run_id(now=started)
    policy = load_execution_policy()
    backup_ran = False
    backup_ok: bool | None = None
    active_hb: list[_DumpHeartbeat | None] = [None]

    def _backup_progress(index: int, total: int, database: str, *, lane: str) -> None:
        if active_hb[0] is not None:
            active_hb[0].__exit__(None, None, None)
            active_hb[0] = None
        # Avoid "[prod n/m]" — Rich markup can mis-parse bracket tags on TTY.
        display_screen.write_summary(f"  {lane} {index}/{total}  {database}")
        active_hb[0] = _DumpHeartbeat(database).__enter__()

    display_screen.write_blank()
    try:
        prod_sources = list(plan.backup_sources)
        production_batch = run_backup_batch(
            BACKUP_KIND_FULL,
            execute=True,
            live=should_probe_database_status(),
            policy=policy,
            sources=prod_sources,
            on_database_start=lambda i, t, d: _backup_progress(i, t, d, lane="prod"),
        )
        if active_hb[0] is not None:
            active_hb[0].__exit__(None, None, None)
            active_hb[0] = None
        backup_ran = True
        backup_ok = bool(getattr(production_batch, "executed_count", 0))
        if backup_ok:
            mark_source_changed_since_package()

        production_verification = None
        if production_batch.executed_count:
            production_verification = verify_written_backup_batch(production_batch)
            display_screen.write_summary(
                f"Prod  {production_batch.executed_count} written · "
                f"{production_verification.verified} verified · "
                f"{production_verification.failed} failed"
            )
            for issue in production_verification.issues:
                display_screen.write_status("fail", issue)
        elif production_batch.refused_count:
            print_backup_batch_result(
                production_batch,
                compact=True,
                menu=True,
                databases_label="Production databases selected",
                suggest_verify=False,
            )
        else:
            display_screen.write_summary(
                f"Prod  {production_batch.executed_count} written · "
                f"{production_batch.refused_count} refused"
            )

        development_batch = None
        development_verification = None
        if include_dev:
            from mercury.backup.batch_runner import resolve_development_backup_sources

            sources = resolve_development_backup_sources(live=should_probe_database_status())
            if not sources:
                display_screen.write_summary("Dev   none present on this server")
            else:
                development_batch = run_backup_batch(
                    BACKUP_KIND_FULL,
                    execute=True,
                    live=should_probe_database_status(),
                    policy=policy,
                    sources=sources,
                    allow_development_backup=True,
                    on_database_start=lambda i, t, d: _backup_progress(
                        i, t, d, lane="dev"
                    ),
                )
                if active_hb[0] is not None:
                    active_hb[0].__exit__(None, None, None)
                    active_hb[0] = None
                if development_batch.executed_count:
                    development_verification = verify_written_backup_batch(
                        development_batch, allow_development_backup=True
                    )
                    display_screen.write_summary(
                        f"Dev   {development_batch.executed_count} written · "
                        f"{development_verification.verified} verified · "
                        f"{development_verification.failed} failed"
                    )
                    for issue in development_verification.issues:
                        display_screen.write_status("fail", issue)
                elif development_batch.refused_count:
                    print_backup_batch_result(
                        development_batch,
                        compact=True,
                        menu=True,
                        databases_label="Development databases selected",
                        suggest_verify=False,
                    )
                else:
                    display_screen.write_summary(
                        f"Dev   {development_batch.executed_count} written · "
                        f"{development_batch.refused_count} refused"
                    )

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

        display_screen.write_blank()
        print_full_backup_run_result(result, lanes_already_shown=True)
        return result
    except Exception:
        backup_ok = False
        raise
    finally:
        if active_hb[0] is not None:
            active_hb[0].__exit__(None, None, None)
            active_hb[0] = None
        note_backup_after_transition(
            availability, backup_ran=backup_ran, backup_succeeded=backup_ok
        )


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


def run_write_database_bundle() -> None:
    """Write DB bundle + runbooks (Deployment and handoff home)."""
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


# Compatibility alias for older call sites / tests.
_write_backup_bundle = run_write_database_bundle


def run_production_backup_flow() -> None:
    """Production-only expert backup entry (hub / programmatic)."""
    _run_backup(_load_plan())


def run_development_backup_flow() -> None:
    """Development-only expert backup entry (hub / programmatic)."""
    _run_development_backup()


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
            from mercury.backup.session_wizard import run_backup_sync_wizard

            run_backup_sync_wizard(interactive=True)
            plan = _load_plan()
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
            _run_development_backup()
            show_title = pause_and_redraw()
            continue

        if choice == "5":
            _run_verify_sources()
            show_title = pause_and_redraw()
            continue

        if choice == "6":
            _preview_backup_plan(plan)
            show_title = pause_and_redraw()
            continue

        output.write(menu_prompts.invalid_choice_message(choice))
