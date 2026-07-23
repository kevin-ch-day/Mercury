"""Interactive Mercury HDD menu — four primary actions; wizards unchanged."""

from __future__ import annotations

from mercury import output
from mercury.core.storage_roles import MigrationState
from mercury.menu import prompts as menu_prompts
from mercury.menu.subscreen import pause_and_redraw, read_submenu_choice, render_submenu
from mercury.storage.cutover_readiness import build_cutover_readiness
from mercury.storage.migrate_plan import build_migration_plan
from mercury.storage.migrate_run import patch_migration_state, run_migration
from mercury.storage.migrate_verify import verify_migration
from mercury.storage.report import build_storage_status_report
from mercury.storage.terminal import (
    print_cutover_readiness,
    print_migration_plan,
    print_migration_run,
    print_migration_verify,
    print_storage_status,
)
from mercury.terminal import screen as display_screen

STORAGE_SCREEN_TITLE = "MERCURY HDD AND STORAGE"


def _package_display(snap) -> str:
    if snap.package_status == "DESTINATION_PACKAGE_VERIFIED" and snap.package_id:
        return "VERIFIED"
    if snap.package_id:
        return snap.package_id
    return "—"


def _render_aligned_fields(fields: list[tuple[str, str]], *, label_width: int = 15) -> None:
    from mercury.terminal.theme import dashboard_row

    for label, value in fields:
        output.write(dashboard_row(label, value, label_width=label_width))


def _render_storage_screen(*, show_title: bool) -> None:
    from mercury.storage.hdd_menu_options import (
        hdd_menu_header_state,
        hdd_menu_render_options,
        recommended_primary_label,
    )
    from mercury.storage.lifecycle import assess_storage_lifecycle

    if show_title:
        display_screen.write_report_header(STORAGE_SCREEN_TITLE)
    snap = assess_storage_lifecycle(probe_disconnect=True)
    storage = "—"
    if snap.device_label or snap.filesystem:
        parts = [p for p in (snap.device_label, snap.filesystem) if p]
        storage = " · ".join(parts) if parts else "—"
    rec_label, _suffix = recommended_primary_label(snap)
    _render_aligned_fields(
        [
            ("Device", snap.device_model or "—"),
            ("Storage", storage),
            ("Mount", snap.mount or "(not mounted)"),
            ("State", hdd_menu_header_state(snap)),
            ("Package", _package_display(snap)),
            ("Recommended", rec_label),
        ]
    )
    display_screen.write_blank()
    render_submenu(hdd_menu_render_options(snap), indent=2)


def _detach_readiness_line() -> str:
    from mercury.storage.detach_wizard import run_detach_preflight
    from mercury.storage.host_maintenance import load_host_maintenance

    host = load_host_maintenance()
    if host.storage_availability == "detached":
        return "Detached"
    try:
        pre = run_detach_preflight(skip_log_redirect=True, mutate_host=False)
    except OSError:
        return "Unknown"
    if pre.result_state == "PREFLIGHT_OK":
        return "Ready"
    if pre.blockers:
        return f"Blocked: {pre.blockers[0][:60]}"
    return pre.result_state


def _run_status_and_validation() -> None:
    """Combined status + validation screen (observe-only; existing report + preflight)."""
    from mercury.core.storage_roles import DEFAULT_PRIMARY_UUID
    from mercury.storage.detach_wizard import run_detach_preflight
    from mercury.storage.host_maintenance import load_host_maintenance, writes_allowed
    from mercury.storage.lifecycle import assess_storage_lifecycle

    display_screen.write_report_header("STORAGE STATUS AND VALIDATION")
    snap = assess_storage_lifecycle(probe_disconnect=True)
    host = load_host_maintenance()

    mount_mode = "—"
    free_space = "—"
    try:
        report = build_storage_status_report()
        # Best-effort free space from existing storage report fields when present.
        free_space = getattr(report, "free_space_label", None) or free_space
        if hasattr(report, "primary") and report.primary is not None:
            free_space = getattr(report.primary, "free_label", free_space) or free_space
    except OSError:
        report = None

    try:
        import subprocess
        from pathlib import Path

        if snap.mount and Path(snap.mount).is_mount():
            completed = subprocess.run(
                ["findmnt", "-n", "-o", "OPTIONS", snap.mount],
                check=False,
                capture_output=True,
                text=True,
            )
            opts = (completed.stdout or "").strip()
            if "ro" in opts.split(",") or opts.startswith("ro"):
                mount_mode = "read-only"
            elif opts:
                mount_mode = "read-write"
    except OSError:
        pass

    _render_aligned_fields(
        [
            ("Device", snap.device_model or "—"),
            ("Label", snap.device_label or "—"),
            ("UUID", snap.device_uuid or "—"),
            ("Filesystem", snap.filesystem or "—"),
            ("Mount", snap.mount or "(not mounted)"),
            ("Mount mode", mount_mode),
            ("Write policy", "disabled" if not writes_allowed(host) else "enabled"),
            (
                "Active writer",
                "none" if not snap.writes_allowed else (snap.active_write_role or "primary"),
            ),
            ("Package", _package_display(snap)),
            ("Free space", str(free_space)),
        ]
    )
    display_screen.write_blank()

    checks: list[tuple[str, bool]] = []
    located = bool(snap.device_uuid or snap.mounted or snap.device_model)
    checks.append(("Mercury HDD located", located))
    uuid_ok = bool(snap.device_uuid) and (
        not DEFAULT_PRIMARY_UUID or snap.device_uuid == DEFAULT_PRIMARY_UUID
    )
    checks.append(("Device UUID matches", uuid_ok))
    fs_ok = (snap.filesystem or "").lower() == "ext4"
    checks.append(("Filesystem matches", fs_ok))
    mount_ok = bool(snap.mount)
    checks.append(("Expected mountpoint", mount_ok))
    pkg_ok = snap.package_status == "DESTINATION_PACKAGE_VERIFIED"
    checks.append(("Destination package verified", pkg_ok))
    writes_off = not writes_allowed(host)
    checks.append(("Mercury writes disabled", writes_off))

    preflight_ok = False
    blockers: list[str] = []
    try:
        pre = run_detach_preflight(skip_log_redirect=True, mutate_host=False)
        preflight_ok = pre.result_state == "PREFLIGHT_OK"
        blockers = list(pre.blockers)
        checks.append(("Safe disconnect ready", preflight_ok))
    except OSError as exc:
        checks.append(("Safe disconnect ready", False))
        blockers = [str(exc)]

    for label, ok in checks:
        tag = "PASS" if ok else "FAIL"
        display_screen.write_status("ok" if ok else "fail", f"[{tag}] {label}")

    if report is not None:
        print_storage_status(report)

    if preflight_ok and writes_off and pkg_ok:
        result = "READY TO DISCONNECT"
    elif not located:
        result = "HDD NOT FOUND"
    elif not uuid_ok:
        result = "IDENTITY MISMATCH"
    elif blockers:
        result = f"BLOCKED · {blockers[0][:60]}"
    else:
        result = f"STATUS · detach readiness: {_detach_readiness_line()}"
    output.write("")
    _render_aligned_fields([("Result", result)])
    display_screen.write_blank()
    render_submenu([("1", "Run validation again")], indent=2)


def run_safe_disconnect_wizard() -> None:
    """Public entry for Safe Disconnect (post Backup and Sync, storage menu)."""
    _run_safe_disconnect_wizard_impl()


def _run_safe_disconnect_wizard() -> None:
    _run_safe_disconnect_wizard_impl()


def _run_safe_disconnect_wizard_impl() -> None:
    from mercury.storage.detach_wizard import (
        DETACH_CONFIRMATION,
        format_wizard_report,
        run_detach_wizard,
    )
    from mercury.storage.block_device import resolve_mercury_block_device

    display_screen.write_report_header("SAFE DISCONNECT MERCURY HDD")
    resolved = resolve_mercury_block_device(require_mounted=False)
    if resolved.identity:
        ident = resolved.identity
        output.field("Drive", ident.label)
        output.field("UUID", ident.uuid)
        output.field("Model", ident.model or "—")
        output.field("Partition", ident.partition_device)
        output.field("Parent", ident.parent_device)
        output.field("Mount", ident.mountpoint or "(not mounted)")
    else:
        for err in resolved.errors:
            display_screen.write_status("fail", err)

    output.write("This operation will:")
    output.write("  1. Check for active users and operations")
    output.write("  2. Flush pending filesystem writes")
    output.write("  3. Unmount the Mercury filesystem")
    output.write("  4. Confirm the filesystem is detached by UUID")
    output.write("  5. Power off the correct external HDD when supported")
    output.write("It will NOT detach MERCURY_DATA_USB, delete files, alter Phase 3B,")
    output.write("resume Erebus, or begin destination validation.")
    if menu_prompts.ask_yes_no("Continue with preflight?", default=False) is not True:
        display_screen.write_summary("Cancelled.")
        return

    preview = run_detach_wizard(execute=False, skip_log_redirect=False)
    while True:
        for line in format_wizard_report(preview):
            output.write(line)
        if preview.ok:
            break
        display_screen.write_status("fail", f"Preflight blocked: {preview.result_state}")
        choice = menu_prompts.ask(
            "Blocked. [R] Recheck  [B] Back  [0] Cancel: "
        ).strip().lower()
        if choice in {"0", "b", "back", "q", "cancel"}:
            display_screen.write_summary("Detach cancelled.")
            return
        if choice not in {"r", "recheck"}:
            display_screen.write_summary("Detach cancelled.")
            return
        preview = run_detach_wizard(execute=False, skip_log_redirect=False)

    output.write("")
    output.write("Administrator access is required to inspect open files and unmount the HDD.")
    output.write("Mercury does not read or store your password.")
    output.write("The standard operating-system sudo prompt may appear.")
    if menu_prompts.ask_yes_no("Proceed to privileged detach?", default=False) is not True:
        display_screen.write_summary("Stopped before privileged steps.")
        return

    phrase = menu_prompts.ask("Type DETACH MERCURY HDD to execute: ").strip()
    if phrase != DETACH_CONFIRMATION:
        display_screen.write_status("fail", "Confirmation phrase mismatch — aborted.")
        return

    while True:
        result = run_detach_wizard(
            execute=True,
            confirm=DETACH_CONFIRMATION,
            skip_log_redirect=False,
            skip_sudo_validate=False,
            power_off=True,
        )
        for line in format_wizard_report(result):
            output.write(line)
        if result.safe_to_physically_disconnect or result.result_state in {
            "DETACH_CANCELLED",
            "DETACH_BLOCKED_SUDO",
            "HDD_ALREADY_DETACHED",
        }:
            return
        choice = menu_prompts.ask(
            "Blocked. [R] Recheck  [B] Back  [0] Cancel: "
        ).strip().lower()
        if choice in {"0", "b", "back", "q", "cancel"}:
            display_screen.write_summary("Detach cancelled.")
            return
        if choice not in {"r", "recheck", ""}:
            display_screen.write_summary("Detach cancelled.")
            return


def _print_reconnect_result(result) -> None:
    output.field("Result", result.result_state)
    output.field("Ok", "yes" if result.ok else "no")
    if result.identity:
        output.field("Partition", str(result.identity.get("partition_device")))
        output.field("Parent", str(result.identity.get("parent_device")))
        output.field("Model", str(result.identity.get("model") or "—"))
    for err in result.errors:
        display_screen.write_status("fail", err)
    for msg in result.messages:
        output.write(f"  {msg}")


def _run_enable_writes() -> None:
    from mercury.storage.lifecycle import StorageLifecycleState, assess_storage_lifecycle
    from mercury.storage.transitions import (
        RESTORE_SOURCE_WRITER_PHRASE,
        restore_source_writer,
    )

    snap = assess_storage_lifecycle(probe_disconnect=False)
    if snap.state in {
        StorageLifecycleState.PREPARING_TO_DISCONNECT,
        StorageLifecycleState.READY_TO_DISCONNECT,
    }:
        display_screen.write_status(
            "fail",
            "Enable writes unavailable · detach preparation active. "
            "Finish or cancel Safe disconnect first, or restore via Backup.",
        )
        return
    if snap.writes_allowed:
        display_screen.write_summary("Mercury writes are already enabled.")
        return
    if snap.state == StorageLifecycleState.DETACHED:
        display_screen.write_status(
            "fail",
            "Attach the HDD physically, then choose Reconnect before enabling writes.",
        )
        return
    phrase = menu_prompts.ask(
        f"Type {RESTORE_SOURCE_WRITER_PHRASE} to enable: "
    ).strip()
    result = restore_source_writer(
        confirm=phrase,
        operator_intent="hdd_menu_enable_writes",
        require_strong_phrase=True,
    )
    if not result.ok:
        display_screen.write_status("fail", "Writes not restored (phrase mismatch or validation).")
        for blocker in result.blockers:
            display_screen.write_status("fail", blocker)
    else:
        display_screen.write_summary("Mercury writes enabled (active writer = primary).")


def _run_disable_writes() -> None:
    from mercury.storage.transitions import disable_writes

    result = disable_writes(operator_intent="hdd_menu_disable_writes")
    display_screen.write_summary(
        result.messages[0] if result.messages else "Mercury writes disabled."
    )


def _run_inspect_readonly(*, execute_mount: bool = False) -> None:
    from mercury.storage.reconnect import run_reconnect_validate

    result = run_reconnect_validate(
        mode="destination",
        execute_mount=execute_mount,
        read_only=True,
    )
    _print_reconnect_result(result)


def _run_restore_source_writer(*, ask_mount: bool = True) -> None:
    from mercury.storage.reconnect import run_reconnect_validate, restore_writes_after_reconnect

    do_mount = False
    if ask_mount:
        do_mount = menu_prompts.ask_yes_no("Start mount now if unmounted?", default=False) is True
    result = run_reconnect_validate(mode="source", execute_mount=do_mount, read_only=False)
    _print_reconnect_result(result)
    if not result.ok:
        return
    if menu_prompts.ask_yes_no(
        "Restore Mercury writes now? (type confirmation next)",
        default=False,
    ):
        phrase = menu_prompts.ask("Type RESTORE MERCURY WRITES: ").strip()
        restored = restore_writes_after_reconnect(confirm=phrase)
        if restored is None:
            display_screen.write_status("fail", "Writes not restored (phrase mismatch).")
        else:
            display_screen.write_summary(
                "Writes restored on source host. Erebus remains paused until separately resumed."
            )


def _run_destination_rehearsal_prep() -> None:
    from mercury.storage.reconnect import run_reconnect_validate
    from mercury.storage.transitions import enter_destination_rehearsal

    result = run_reconnect_validate(mode="destination", execute_mount=True, read_only=True)
    _print_reconnect_result(result)
    if result.ok:
        transition = enter_destination_rehearsal(
            operator_intent="hdd_menu_destination_rehearsal"
        )
        if not transition.ok:
            display_screen.write_status(
                "fail",
                "Destination rehearsal flag was not recorded: "
                + ("; ".join(transition.blockers) or transition.status.value),
            )
            return
        display_screen.write_summary(
            "Destination rehearsal prepared. Writes stay disabled. "
            "Destination validation is NOT complete."
        )


def _run_prepare_disconnect() -> None:
    """Disable writes via existing policy helper, then optionally open safe disconnect."""
    _run_disable_writes()
    if menu_prompts.ask_yes_no("Open Safe disconnect wizard now?", default=False) is True:
        _run_safe_disconnect_wizard()


def _run_change_mode_menu() -> None:
    from mercury.storage.hdd_menu_options import (
        MODE_CONTINUE_RO,
        MODE_DESTINATION_REHEARSAL,
        MODE_DISABLE_WRITES,
        MODE_INSPECT_RO,
        MODE_KEEP_WRITES_DISABLED,
        MODE_PREPARE_DISCONNECT,
        MODE_RESTORE_WRITER,
        change_mode_options,
        host_role_header,
    )
    from mercury.storage.lifecycle import assess_storage_lifecycle

    snap = assess_storage_lifecycle(probe_disconnect=True)
    display_screen.write_report_header("RECONNECT OR CHANGE STORAGE MODE")
    _render_aligned_fields(
        [
            ("Current state", snap.label),
            ("Host role", host_role_header(snap)),
        ]
    )
    display_screen.write_blank()
    mode_opts = change_mode_options(snap)
    render_submenu([(k, label) for k, label, _a in mode_opts], indent=2)
    choice = read_submenu_choice()
    if choice is None or choice == "0":
        return
    action_by_key = {k: a for k, _label, a in mode_opts}
    action = action_by_key.get(choice)
    if action is None:
        output.write(menu_prompts.invalid_choice_message(choice))
        return
    detached = snap.state.value in {"DETACHED", "DEVICE_NOT_FOUND"}
    if action == MODE_INSPECT_RO:
        _run_inspect_readonly(execute_mount=detached)
        return
    if action == MODE_CONTINUE_RO:
        _run_inspect_readonly(execute_mount=False)
        return
    if action == MODE_RESTORE_WRITER:
        _run_restore_source_writer(ask_mount=True)
        return
    if action == MODE_DISABLE_WRITES:
        _run_disable_writes()
        return
    if action == MODE_PREPARE_DISCONNECT:
        _run_prepare_disconnect()
        return
    if action == MODE_KEEP_WRITES_DISABLED:
        display_screen.write_summary("Writes remain disabled. No policy change.")
        return
    if action == MODE_DESTINATION_REHEARSAL:
        _run_destination_rehearsal_prep()
        return
    output.write(menu_prompts.invalid_choice_message(choice))


def _run_recommended_action() -> None:
    """Launch the wizard that matches option [1] for the current lifecycle state."""
    from mercury.storage.hdd_menu_options import recommended_primary_label
    from mercury.storage.lifecycle import StorageLifecycleState, assess_storage_lifecycle

    snap = assess_storage_lifecycle(probe_disconnect=True)
    label, _suffix = recommended_primary_label(snap)
    state = snap.state

    if "Recheck disconnect" in label:
        _run_status_and_validation()
        return
    if "Verify destination package" in label:
        _run_status_and_validation()
        return
    if state == StorageLifecycleState.DEVICE_IDENTITY_MISMATCH or "Diagnose" in label:
        _run_status_and_validation()
        return
    if state in {
        StorageLifecycleState.DETACHED,
        StorageLifecycleState.DEVICE_NOT_FOUND,
    } or label.startswith("Reconnect or inspect"):
        _run_change_mode_menu()
        return
    if state == StorageLifecycleState.ATTACHED_READ_ONLY or "destination inspection" in label.lower():
        _run_inspect_readonly(execute_mount=False)
        return
    if state == StorageLifecycleState.ATTACHED_WRITER_ENABLED or "Prepare HDD" in label:
        _run_change_mode_menu()
        return
    if "Safe disconnect" in label:
        _run_safe_disconnect_wizard()
        return
    if "Storage status" in label or state == StorageLifecycleState.ATTACHED_UNVALIDATED:
        _run_status_and_validation()
        return
    _run_change_mode_menu()


def _run_cleanup_advanced_menu() -> None:
    from mercury.storage.hdd_menu_options import (
        ADV_ARCHIVE_USB,
        ADV_CLEANUP_PREVIEW,
        ADV_CLEANUP_STATUS,
        ADV_DEVICE_DETAIL,
        ADV_SMART,
        ADV_TROUBLESHOOT,
        cleanup_advanced_options,
    )
    from mercury.storage.host_maintenance import load_host_maintenance
    from mercury.storage.retention import load_retention_policy

    host = load_host_maintenance()
    policy = load_retention_policy()
    cleanup_locked = True
    if host.package_verification_status == "DESTINATION_PACKAGE_VERIFIED":
        # Destination validation pending / rehearsal — keep destructive cleanup locked.
        cleanup_locked = True

    display_screen.write_report_header("CLEANUP AND ADVANCED STORAGE")
    _render_aligned_fields(
        [
            ("Cleanup", "Preview only"),
            (
                "Execution",
                "Locked until destination validation" if cleanup_locked else "Available",
            ),
            (
                "Safe candidates",
                f"Approximately {policy.safe_candidate_estimate_gib:.1f} GiB",
            ),
            (
                "Manual review",
                f"Approximately {policy.manual_review_project_estimate_gib:.1f} GiB",
            ),
        ]
    )
    display_screen.write_blank()
    opts = cleanup_advanced_options(cleanup_locked=cleanup_locked)
    render_submenu([(k, label) for k, label, _a in opts], indent=2)
    choice = read_submenu_choice()
    if choice is None or choice == "0":
        return
    action_by_key = {k: a for k, _label, a in opts}
    action = action_by_key.get(choice)
    if action is None:
        output.write(menu_prompts.invalid_choice_message(choice))
        return
    if action in {ADV_CLEANUP_STATUS, ADV_CLEANUP_PREVIEW}:
        display_screen.write_summary(
            f"Cleanup preview locked · ~{policy.safe_candidate_estimate_gib:.1f} GiB candidates"
        )
        display_screen.write_hint(
            "Execution remains locked until destination cutover policy allows it."
        )
        if cleanup_locked:
            display_screen.write_status("warn", "Destructive cleanup is unavailable.")
        return
    if action == ADV_DEVICE_DETAIL:
        print_storage_status(build_storage_status_report())
        return
    if action == ADV_SMART:
        from mercury.storage.smart_health import build_smart_health_plan

        plan = build_smart_health_plan()
        display_screen.write_summary(
            f"Primary device: {plan['block_device'] or 'unknown'} · {plan['command']}"
        )
        display_screen.write_hint(f"Receipt: {plan['receipt_path']}")
        return
    if action == ADV_ARCHIVE_USB:
        from mercury.storage.archive_remount import build_archive_remount_plan

        plan = build_archive_remount_plan()
        display_screen.write_summary(f"Current USB mode: {plan.current_mode}")
        display_screen.write_hint(plan.remount_command)
        for note in plan.notes:
            display_screen.write_hint(note)
        return
    if action == ADV_TROUBLESHOOT:
        _run_troubleshoot_menu()
        return
    output.write(menu_prompts.invalid_choice_message(choice))


def _run_troubleshoot_menu() -> None:
    display_screen.write_report_header("STORAGE TROUBLESHOOTING")
    render_submenu(
        [
            ("1", "Migration plan"),
            ("2", "Preview migration"),
            ("3", "Verify mirror"),
            ("4", "Cutover readiness"),
            ("5", "Record migration plan"),
            ("0", "Back"),
        ],
        indent=0,
    )
    choice = read_submenu_choice()
    if choice is None or choice == "0":
        return
    if choice == "1":
        print_migration_plan(build_migration_plan())
        return
    if choice == "2":
        print_migration_run(run_migration(execute=False, update_state=False))
        return
    if choice == "3":
        print_migration_verify(verify_migration(update_state=False))
        return
    if choice == "4":
        print_cutover_readiness(build_cutover_readiness())
        return
    if choice == "5":
        plan = build_migration_plan()
        if not plan.ready_for_migrate_execute:
            display_screen.write_status(
                "fail",
                "Plan not ready — resolve blockers before marking planned.",
            )
        else:
            notes = patch_migration_state(MigrationState.PLANNED)
            for note in notes:
                display_screen.write_status("warn", note)
            display_screen.write_summary(
                "migration_state=planned (writers still on legacy until cutover)."
            )
        return
    output.write(menu_prompts.invalid_choice_message(choice))


def run_storage_menu(*, interactive: bool = True) -> None:
    """First-class Mercury HDD lifecycle menu (four primary actions)."""
    from mercury.storage.hdd_menu_options import (
        STORAGE_CHANGE_MODE,
        STORAGE_MAINTENANCE,
        STORAGE_RECOMMENDED_ACTION,
        STORAGE_STATUS_VALIDATE,
        hdd_menu_option_by_action,
    )

    show_title = True
    while True:
        _render_storage_screen(show_title=show_title)
        show_title = False
        if not interactive:
            return
        choice = read_submenu_choice()
        if choice is None or choice == "0":
            return

        key_rec, _ = hdd_menu_option_by_action(STORAGE_RECOMMENDED_ACTION)
        key_status, _ = hdd_menu_option_by_action(STORAGE_STATUS_VALIDATE)
        key_mode, _ = hdd_menu_option_by_action(STORAGE_CHANGE_MODE)
        key_adv, _ = hdd_menu_option_by_action(STORAGE_MAINTENANCE)

        if choice == key_rec:
            _run_recommended_action()
            show_title = pause_and_redraw()
            continue
        if choice == key_status:
            while True:
                _run_status_and_validation()
                sub = read_submenu_choice()
                if sub is None or sub == "0":
                    break
                if sub == "1":
                    continue
                output.write(menu_prompts.invalid_choice_message(sub or ""))
                break
            show_title = pause_and_redraw()
            continue
        if choice == key_mode:
            _run_change_mode_menu()
            show_title = pause_and_redraw()
            continue
        if choice == key_adv:
            _run_cleanup_advanced_menu()
            show_title = pause_and_redraw()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))
