"""Interactive storage status, migration helpers, and safe disconnect wizard."""

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

STORAGE_SCREEN_TITLE = "Storage Operations"


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


def _render_storage_screen(*, show_title: bool) -> None:
    if show_title:
        display_screen.write_report_header(STORAGE_SCREEN_TITLE)
    report = build_storage_status_report()
    print_storage_status(report)
    from mercury.storage.block_device import resolve_mercury_block_device
    from mercury.storage.detach_wizard import latest_verified_package
    from mercury.core.usb_mount import resolve_operator_mount
    from mercury.storage.host_maintenance import load_host_maintenance

    host = load_host_maintenance()
    mount = resolve_operator_mount()
    pkg_id, pkg_status = latest_verified_package(mount)
    resolved = resolve_mercury_block_device(require_mounted=False)
    device_line = "UUID unresolved"
    if resolved.identity:
        ident = resolved.identity
        device_line = (
            f"{ident.label or 'MERCURY_DATA_V2'} · {ident.model or 'model?'} · "
            f"{ident.partition_device}"
        )
    writer = (
        f"HDD · {host.storage_availability} · "
        f"{'package verified' if pkg_status == 'DESTINATION_PACKAGE_VERIFIED' else 'package pending'}"
    )
    display_screen.write_blank()
    display_screen.write_section("Safe disconnect")
    output.field("Active writer", writer)
    output.field("Device", device_line)
    output.field("Package", pkg_id or "—")
    output.field("Detach readiness", _detach_readiness_line())
    display_screen.write_blank()
    display_screen.write_section("Actions")
    render_submenu(
        [
            ("1", "Storage status"),
            ("2", "Validate active writer / refresh"),
            ("3", "Safe disconnect Mercury HDD"),
            ("4", "Reconnect / validate returned HDD"),
            ("5", "Migration plan"),
            ("6", "Preview migration"),
            ("7", "Verify mirror"),
            ("8", "Cutover readiness"),
            ("9", "USB archive remount RO (preview)"),
            ("10", "HDD SMART health (preview)"),
            ("11", "Record migration plan"),
        ],
        indent=0,
    )


def _run_safe_disconnect_wizard() -> None:
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

    # Live privileged path — operator-driven only from this menu.
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
        # Recheck continues the loop


def _run_reconnect_wizard() -> None:
    from mercury.storage.reconnect import run_reconnect_validate, restore_writes_after_reconnect

    display_screen.write_report_header("RECONNECT / VALIDATE MERCURY HDD")
    output.write("Resolves the drive by filesystem UUID (never a fixed /dev letter).")
    mode_choice = menu_prompts.ask(
        "Mode: [1] Source host  [2] Destination read-only inspect  [0] Cancel: "
    ).strip()
    if mode_choice in {"0", ""}:
        return
    destination = mode_choice == "2"
    do_mount = menu_prompts.ask_yes_no(
        "Start mount now if unmounted?", default=False
    ) is True
    result = run_reconnect_validate(
        mode="destination" if destination else "source",
        execute_mount=do_mount,
        read_only=destination,
    )
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
    if result.ok and not destination:
        if menu_prompts.ask_yes_no(
            "Restore Mercury writes now? (usually NO until validation finishes)",
            default=False,
        ):
            phrase = menu_prompts.ask("Type RESTORE MERCURY WRITES: ").strip()
            restored = restore_writes_after_reconnect(confirm=phrase)
            if restored is None:
                display_screen.write_status("fail", "Writes not restored (phrase mismatch).")
            else:
                display_screen.write_summary("Writes restored on source host.")


def run_storage_menu(*, interactive: bool = True) -> None:
    """Operator console for storage roots, disconnect, and migration."""
    show_title = True
    while True:
        _render_storage_screen(show_title=show_title)
        show_title = False
        if not interactive:
            return
        choice = read_submenu_choice()
        if choice is None or choice == "0":
            return
        if choice == "1":
            display_screen.write_summary("Refreshed storage status.")
            show_title = pause_and_redraw()
            continue
        if choice == "2":
            display_screen.write_summary("Active writer / detach readiness refreshed.")
            show_title = pause_and_redraw()
            continue
        if choice == "3":
            _run_safe_disconnect_wizard()
            show_title = pause_and_redraw()
            continue
        if choice == "4":
            _run_reconnect_wizard()
            show_title = pause_and_redraw()
            continue
        if choice == "5":
            plan = build_migration_plan()
            print_migration_plan(plan)
            show_title = pause_and_redraw()
            continue
        if choice == "6":
            result = run_migration(execute=False, update_state=False)
            print_migration_run(result)
            show_title = pause_and_redraw()
            continue
        if choice == "7":
            report = verify_migration(update_state=False)
            print_migration_verify(report)
            show_title = pause_and_redraw()
            continue
        if choice == "8":
            print_cutover_readiness(build_cutover_readiness())
            show_title = pause_and_redraw()
            continue
        if choice == "9":
            from mercury.storage.archive_remount import build_archive_remount_plan

            plan = build_archive_remount_plan()
            display_screen.write_summary(f"Current USB mode: {plan.current_mode}")
            display_screen.write_hint(plan.remount_command)
            for note in plan.notes:
                display_screen.write_hint(note)
            show_title = pause_and_redraw()
            continue
        if choice == "10":
            from mercury.storage.smart_health import build_smart_health_plan

            plan = build_smart_health_plan()
            display_screen.write_summary(
                f"Primary device: {plan['block_device'] or 'unknown'} · {plan['command']}"
            )
            display_screen.write_hint(f"Receipt: {plan['receipt_path']}")
            show_title = pause_and_redraw()
            continue
        if choice == "11":
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
                    "migration_state=planned (writers still on legacy)."
                )
            show_title = pause_and_redraw()
            continue
        output.write(menu_prompts.invalid_choice_message(choice))
