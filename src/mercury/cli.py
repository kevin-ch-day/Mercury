"""Mercury command-line interface."""

import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from mercury import output

app = typer.Typer(
    name="mercury",
    help="Mercury — database backup, DR, and sync-readiness utility (seed / dry-run).",
    no_args_is_help=True,
    invoke_without_command=True,
)

env_app = typer.Typer(help="Environment commands.")
db_app = typer.Typer(help="Database commands.")
database_app = typer.Typer(help="Database module (same commands as db).")
backup_app = typer.Typer(help="Backup commands.")
repo_app = typer.Typer(help="Repository protection and transfer commands.")
transfer_app = typer.Typer(help="Combined database + repository transfer manifests and runbooks.")
config_app = typer.Typer(help="Configuration commands.")
sync_app = typer.Typer(help="Production sync-pair planning and execution.")
restore_app = typer.Typer(help="Restore-check and DR execution.")
deploy_app = typer.Typer(help="Deploy verified operator-storage backups onto this MariaDB host.")
report_app = typer.Typer(help="Backup report previews (dry-run).")
logs_app = typer.Typer(help="Mercury log files under logs/.")
state_app = typer.Typer(help="Portable Mercury operation ledger and summaries.")
storage_app = typer.Typer(
    help="Primary/legacy storage status, audit, validate, migrate, and cutover tools."
)
migration_app = typer.Typer(help="Read-only workstation migration readiness and blockers.")

app.add_typer(env_app, name="env")
app.add_typer(db_app, name="db")
app.add_typer(database_app, name="database")
app.add_typer(backup_app, name="backup")
app.add_typer(repo_app, name="repo")
app.add_typer(transfer_app, name="transfer")
app.add_typer(config_app, name="config")
app.add_typer(sync_app, name="sync")
app.add_typer(restore_app, name="restore-check")
app.add_typer(deploy_app, name="deploy")
app.add_typer(report_app, name="report")
app.add_typer(logs_app, name="logs")
app.add_typer(state_app, name="state")
app.add_typer(storage_app, name="storage")
app.add_typer(migration_app, name="migration")
repair_app = typer.Typer(help="Host repair helpers.")
app.add_typer(repair_app, name="repair")


@app.callback()
def mercury_main(
    ctx: typer.Context,
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        help="File log level: DEBUG, INFO, WARNING, ERROR.",
        envvar="MERCURY_LOG_LEVEL",
    ),
    log_dir: Optional[str] = typer.Option(
        None,
        "--log-dir",
        help="Directory for log files (default: logs/).",
        envvar="MERCURY_LOG_DIR",
    ),
    logging_enabled: Optional[bool] = typer.Option(
        None,
        "--logging/--no-logging",
        help="Enable or disable file logging.",
    ),
) -> None:
    """Mercury CLI — file logs written to logs/mercury-YYYY-MM-DD.log by default."""
    from mercury.bootstrap import init_command_logging

    init_command_logging(
        invoked_subcommand=ctx.invoked_subcommand,
        log_level=log_level,
        log_dir=log_dir,
        logging_enabled=logging_enabled,
    )


@env_app.command("probe")
def env_probe(
    check_db: bool = typer.Option(
        False,
        "--check-db",
        help="Run read-only MariaDB probe when config/local.toml is configured.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Include tooling paths, config details, and safety policy text.",
    ),
) -> None:
    """Probe the local environment (optional read-only database check)."""
    from mercury.core.execution_policy import load_execution_policy
    from mercury.core.runtime import operator_status, should_probe_database_status
    from mercury.database.terminal.ping import print_server_probe
    from mercury.database import (
        MariaDbConfigError,
        MariaDbDriverMissingError,
        MariaDbLiveError,
        probe_mariadb_server,
    )
    from mercury.env.probe import format_policy_summary, probe_environment

    result = probe_environment(check_database=check_db)
    policy = load_execution_policy()

    output.heading("Environment")
    output.field("python", result.python_version)
    output.field("platform", f"{result.platform_system} ({result.platform_release})")
    output.field("platform_support", result.platform_support)
    output.field("mode", result.mode)
    output.field("dry_run", policy.dry_run)
    output.field("live_actions", policy.live_actions_enabled)

    from mercury.core.runtime import operator_status

    status = operator_status(probe_database=should_probe_database_status())
    output.field("database", status["database"])
    output.field("backup_root", status["backup_root"])

    if verbose:
        output.field("repo_root", result.repo_root)
        from mercury.database import probe_client_tooling

        tooling = probe_client_tooling()
        output.heading("MariaDB client tools")
        for name, path in tooling.tools.items():
            output.field(name, path)
        output.heading("Config status")
        for key, value in result.config_status.items():
            output.field(key, value)
        for note in result.notes:
            output.bullet(note)

    if check_db:
        try:
            print_server_probe(probe_mariadb_server(), compact=not verbose)
        except MariaDbConfigError as exc:
            output.field("config_error", str(exc))
        except MariaDbDriverMissingError as exc:
            output.field("driver_error", str(exc))
        except MariaDbLiveError as exc:
            output.field("connection_error", str(exc))
    elif verbose:
        for note in result.notes:
            output.bullet(note)

    from mercury.logging.events import log_env_probe

    connected = "connected" in status["database"].lower() and "not connected" not in status["database"].lower()
    log_env_probe(connected=connected, database_status=status["database"])

    if verbose:
        output.heading("Safety policy")
        for line in format_policy_summary().splitlines():
            output.write(line)


@state_app.command("summary")
def state_summary() -> None:
    """Show Mercury's portable ledger root and recorded operation counts."""
    from mercury.state.summary import build_state_summary, print_state_summary

    print_state_summary(build_state_summary())


@state_app.command("handoff-history")
def state_handoff_history_cmd(
    limit: int = typer.Option(
        12,
        "--limit",
        min=1,
        max=50,
        help="Maximum handoff history rows to show.",
    ),
) -> None:
    """Show recent handoff events recorded on the USB state ledger."""
    from mercury.handoff.history import build_handoff_history
    from mercury.handoff.terminal import print_handoff_history

    print_handoff_history(build_handoff_history(limit=limit))


@storage_app.command("status")
def storage_status_cmd() -> None:
    """Show primary and legacy storage roles (observe-only; does not switch writers)."""
    from mercury.storage.report import build_storage_status_report
    from mercury.storage.terminal import print_storage_status

    print_storage_status(build_storage_status_report())


@storage_app.command("validate")
def storage_validate_cmd() -> None:
    """Validate configured storage mounts; exit non-zero if the active writer fails."""
    from mercury.storage.report import build_storage_status_report
    from mercury.storage.terminal import print_storage_validate

    code = print_storage_validate(build_storage_status_report())
    if code:
        raise typer.Exit(code)


@storage_app.command("archive-receipt")
def storage_archive_receipt_cmd(
    execute: bool = typer.Option(False, "--execute", help="Write the immutable USB archive receipt on the HDD."),
    override: bool = typer.Option(False, "--administrative-override", help="Replace an existing historical receipt (administrative use only)."),
    confirm: str | None = typer.Option(None, "--confirm", help="Required with override: REPLACE USB ARCHIVE RECEIPT."),
    full_manifest: bool = typer.Option(
        False,
        "--full-manifest",
        help="Preview with a full relative-path manifest (slower). Execute always builds the full manifest.",
    ),
) -> None:
    """Preview or record USB recovery-archive evidence; never writes to USB."""
    from mercury.storage.archive_receipt import build_archive_receipt, record_archive_receipt

    if override and (not execute or confirm != "REPLACE USB ARCHIVE RECEIPT"):
        raise typer.BadParameter("--administrative-override requires --execute --confirm 'REPLACE USB ARCHIVE RECEIPT'")
    try:
        if execute:
            result = record_archive_receipt(override=override)
        else:
            result = build_archive_receipt(include_path_manifest=full_manifest)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output.heading("USB Archive Receipt")
    output.field("Mode", "EXECUTE" if execute else "PREVIEW")
    output.field("Application policy", result.payload["application_policy"])
    output.field("Filesystem mode", result.payload["filesystem_mount_mode"])
    output.field("Archive generation", result.payload["final_usb_archive_generation"])
    output.field("Manifest SHA-256", result.payload["manifest_sha256"])
    output.field("Receipt", str(result.path) if execute else f"would write {result.path}")
    output.field("Physical retirement", "not authorized")
    if not execute:
        output.write("\nPreview only. No USB or HDD artifact was written.")
        if not full_manifest:
            output.write("Path manifest omitted for speed; use --full-manifest or --execute for the durable list.")


@storage_app.command("archive-remount-ro")
def storage_archive_remount_ro_cmd(
    execute: bool = typer.Option(False, "--execute", help="Remount the USB archive read-only (requires sudo)."),
    confirm: str | None = typer.Option(
        None,
        "--confirm",
        help="Confirmation phrase for --execute (REMOUNT ARCHIVE RO).",
    ),
) -> None:
    """Preview or remount the USB recovery archive read-only. Never touches the HDD writer."""
    from mercury.storage.archive_remount import (
        ARCHIVE_REMOUNT_RO_CONFIRMATION,
        build_archive_remount_plan,
        execute_archive_remount_ro,
    )

    if not execute:
        plan = build_archive_remount_plan()
        output.heading("USB Archive Remount Read-Only")
        output.field("Mode", "PREVIEW")
        output.field("Mount", str(plan.mount_path))
        output.field("UUID", plan.filesystem_uuid)
        output.field("Label", plan.label)
        output.field("Current mode", plan.current_mode)
        output.field("Command", plan.remount_command)
        output.field("Confirmation", plan.confirmation_phrase)
        for blocker in plan.blockers:
            output.item(f"Blocker: {blocker}")
        for note in plan.notes:
            output.item(note)
        if plan.already_read_only:
            output.write("\nAlready read-only — nothing to do.")
        else:
            output.write(
                f"\nPreview only. Re-run with --execute --confirm '{ARCHIVE_REMOUNT_RO_CONFIRMATION}'."
            )
        if plan.blockers:
            raise typer.Exit(1)
        return

    confirmation = confirm
    if confirmation is None:
        confirmation = typer.prompt(f"Type {ARCHIVE_REMOUNT_RO_CONFIRMATION} to remount USB archive read-only")
    result = execute_archive_remount_ro(confirmation=confirmation)
    output.heading("USB Archive Remount Read-Only")
    output.field("Mode", "EXECUTE")
    output.field("Success", "yes" if result.success else "no")
    output.field("Executed", "yes" if result.executed else "no")
    output.field("Mode after", result.mode_after or "unknown")
    output.field("Detail", result.message)
    output.field("Command", result.plan.remount_command)
    if not result.success:
        raise typer.Exit(1)


@storage_app.command("smart-health")
def storage_smart_health_cmd(
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Run sudo smartctl against the primary HDD and record evidence under .mercury_control/smart/.",
    ),
) -> None:
    """Preview or record primary HDD SMART health evidence (never writes to USB)."""
    from mercury.storage.smart_health import build_smart_health_plan, record_smart_health

    if not execute:
        plan = build_smart_health_plan()
        output.heading("HDD SMART Health")
        output.field("Mode", "PREVIEW")
        output.field("Mount", plan["mount_path"])
        output.field("UUID", plan["filesystem_uuid"])
        output.field("Block device", plan["block_device"] or "unknown")
        output.field("smartctl", plan["smartctl"] or "not found")
        output.field("Command", plan["command"])
        output.field("Receipt", plan["receipt_path"])
        existing = plan.get("existing")
        if existing:
            output.field("Existing recorded", existing.get("recorded_at_utc"))
            output.field("Existing health passed", existing.get("overall_health_passed"))
        output.write("\nPreview only. Re-run with --execute (requires interactive sudo).")
        return

    result = record_smart_health()
    output.heading("HDD SMART Health")
    output.field("Mode", "EXECUTE")
    output.field("Success", "yes" if result.success else "no")
    output.field("Receipt", str(result.path) if result.path.exists() else f"not written ({result.message})")
    output.field("Detail", result.message)
    if result.payload.get("block_device"):
        output.field("Block device", result.payload["block_device"])
    if not result.success:
        raise typer.Exit(1)

@migration_app.command("blockers")
def migration_blockers_cmd() -> None:
    """Show unresolved workstation-migration checks without changing state."""
    from mercury.migration.readiness import build_migration_readiness
    from mercury.migration.terminal import print_migration_blockers

    code = print_migration_blockers(build_migration_readiness())
    if code:
        raise typer.Exit(code)


@migration_app.command("next")
def migration_next_cmd() -> None:
    """Show the single highest-priority read-only migration action."""
    from mercury.migration.readiness import build_migration_readiness
    from mercury.migration.terminal import print_migration_next

    code = print_migration_next(build_migration_readiness())
    if code:
        raise typer.Exit(code)


@migration_app.command("package-status")
def migration_package_status_cmd() -> None:
    """Show historical cutover evidence and the authoritative active package."""
    from mercury.core.storage_roots import load_storage_config
    from mercury.migration.generation import (
        build_active_hdd_generation, build_usb_generation, read_archive_receipt,
        read_cutover_receipt, read_verified_generation,
    )
    from mercury.migration.readiness import _repo_checks

    config = load_storage_config(warn_deprecated=False)
    output.heading("Migration Package Status")
    if config.cutover_complete:
        active = build_active_hdd_generation(config=config)
        cutover = read_cutover_receipt(config=config)
        verified = read_verified_generation(config=config)
        archive = read_archive_receipt(config=config)
        erebus, scytale, _runtime, repos = _repo_checks()
        current_web = sum(check.state.value == "PASS" for check in (erebus, scytale))
        output.write("  Active package: HDD")
        output.write(f"  HDD package generation: {active.generation}")
        output.write(f"  Final USB archive generation: {(cutover or {}).get('final_usb_archive_generation') or verified or 'missing'}")
        output.write(f"  Cutover HDD generation: {(cutover or {}).get('cutover_verified_hdd_generation') or verified or 'missing'}")
        output.write(f"  Cutover receipt: {'Recorded' if cutover else 'Historical record missing'}")
        output.write(f"  Web snapshots: {current_web} current · restore checked")
        output.write(f"  Repository worktrees: {repos.summary}")
        output.write(f"  USB archive: {'Receipt recorded' if archive else 'Receipt missing'}")
        output.write("  Destination PC: Not validated")
    else:
        generation = build_usb_generation(config=config)
        verified = read_verified_generation(config=config)
        output.write("  Active package: USB")
        output.write(f"  USB generation: {generation.generation}")
        output.write(f"  USB durable entries: {generation.durable_entries}")
        output.write(f"  USB durable files: {generation.durable_files}")
        output.write(f"  HDD verified generation: {verified or 'none'}")
        output.write(f"  HDD mirror: {'Current' if verified == generation.generation else 'Refresh required'}")


@migration_app.command("capture-web")
def migration_capture_web_cmd(
    execute: bool = typer.Option(False, "--execute", help="Write restricted snapshots to the active operator storage."),
) -> None:
    """Preview or capture dirty web worktrees; source repositories are never modified."""
    from mercury.migration.web_capture import capture_web_worktrees

    results = capture_web_worktrees(execute=execute)
    output.heading("Web Worktree Capture")
    output.field("Mode", "EXECUTE" if execute else "PREVIEW")
    for result in results:
        outcome = "restore checked" if result.restore_checked else ("preview" if not execute else "failed")
        output.write(f"  {result.name}: {outcome} · {result.snapshot_dir}")
        if result.error:
            output.write(f"    error: {result.error}")
    if not execute:
        output.write("\nPreview only. Re-run with --execute to write restricted snapshots.")
    if any(result.error for result in results):
        raise typer.Exit(1)


@migration_app.command("capture-worktrees")
def migration_capture_worktrees_cmd(
    execute: bool = typer.Option(False, "--execute", help="Write restricted snapshots to the active HDD."),
    repo: list[str] = typer.Option(None, "--repo", help="Configured repository key to capture (repeatable)."),
) -> None:
    """Preview or capture dirty configured repositories; source repositories are never modified."""
    from mercury.migration.web_capture import capture_worktrees
    from mercury.repo.config import RepoSelectionError

    try:
        results = capture_worktrees(execute=execute, keys=set(repo) if repo else None)
    except RepoSelectionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output.heading("Worktree Capture")
    output.field("Mode", "EXECUTE" if execute else "PREVIEW")
    if not results:
        output.write("  No selected dirty configured worktrees require capture.")
    for result in results:
        outcome = "restore checked" if result.restore_checked else ("preview" if not execute else "failed")
        output.write(f"  {result.name}: {outcome} · {result.snapshot_dir}")
        if result.error:
            output.write(f"    error: {result.error}")
    if not execute:
        output.write("\nPreview only. Re-run with --execute to write restricted snapshots.")
    if any(result.error for result in results):
        raise typer.Exit(1)


@storage_app.command("audit")
def storage_audit_cmd(
    hash_files: bool = typer.Option(
        False,
        "--hash",
        help="Also SHA-256 every legacy file against primary (can take time; use --no-logging for strict no-write audit).",
    ),
    write_report: bool = typer.Option(
        False,
        "--write-report",
        help="Write JSON under output/storage/ (never writes to either storage volume).",
    ),
    report_path: Path | None = typer.Option(
        None,
        "--report-path",
        help="Explicit JSON report path (implies --write-report).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the structured report as JSON.",
    ),
) -> None:
    """Audit configured legacy→primary storage; never copies or switches writers."""
    from mercury.storage.audit import build_storage_audit, write_storage_audit_report
    from mercury.storage.terminal import print_storage_audit

    report = build_storage_audit(hash_files=hash_files)
    written = None
    if write_report or report_path is not None:
        written = write_storage_audit_report(report, report_path)
    if json_output:
        import json

        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        code = report.exit_code
    else:
        code = print_storage_audit(report)
        if written is not None:
            output.write(f"JSON report: {written}")
    if code:
        raise typer.Exit(code)


@storage_app.command("migrate-plan")
def storage_migrate_plan_cmd(
    write_report: bool = typer.Option(
        False,
        "--write-report",
        help="Write JSON plan under output/storage/ (repo artifact only; no volume writes).",
    ),
    report_path: Path | None = typer.Option(
        None,
        "--report-path",
        help="Optional explicit JSON report path (implies --write-report).",
    ),
    update_state: bool = typer.Option(
        False,
        "--update-state",
        help="When the plan is ready, set [storage].migration_state=planned in local.toml.",
    ),
) -> None:
    """Dry-run inventory: plan legacy → primary copy (never copies or switches writers)."""
    from mercury.core.storage_roles import MigrationState
    from mercury.storage.migrate_plan import (
        build_migration_plan,
        write_migration_plan_report,
    )
    from mercury.storage.migrate_run import patch_migration_state
    from mercury.storage.terminal import print_migration_plan

    report = build_migration_plan()
    written: str | None = None
    if write_report or report_path is not None:
        path = write_migration_plan_report(report, report_path)
        written = str(path)
    code = print_migration_plan(report, report_path=written)
    if update_state and report.ready_for_migrate_execute:
        for note in patch_migration_state(MigrationState.PLANNED):
            from mercury.terminal import screen as display_screen

            display_screen.write_hint(note)
    elif update_state and not report.ready_for_migrate_execute:
        from mercury.terminal import screen as display_screen

        display_screen.write_status(
            "fail",
            "Skipped --update-state: plan is not ready.",
        )
        code = code or 1
    if code:
        raise typer.Exit(code)


@storage_app.command("migrate-run")
def storage_migrate_run_cmd(
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Perform the copy to primary (default is dry-run preview).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Non-interactive confirm (still requires --confirm 'MIGRATE PRIMARY').",
    ),
    confirm: str | None = typer.Option(
        None,
        "--confirm",
        help="Confirmation phrase for --execute (MIGRATE PRIMARY).",
    ),
    update_state: bool = typer.Option(
        True,
        "--update-state/--no-update-state",
        help="After successful copy, set [storage].migration_state=copied in local.toml.",
    ),
    write_report: bool = typer.Option(
        False,
        "--write-report",
        help="Also write a JSON report under output/storage/.",
    ),
) -> None:
    """Copy legacy → primary (dry-run by default). Does not switch writers or cut over."""
    from mercury.core.safety import MIGRATE_PRIMARY_CONFIRMATION_PHRASE
    from mercury.storage.migrate_run import run_migration
    from mercury.storage.terminal import print_migration_run

    confirmation = confirm
    if execute and confirmation is None:
        if yes:
            raise typer.BadParameter(
                f"--yes requires --confirm '{MIGRATE_PRIMARY_CONFIRMATION_PHRASE}'"
            )
        confirmation = typer.prompt(
            f"Type {MIGRATE_PRIMARY_CONFIRMATION_PHRASE} to copy onto primary "
            "(writers stay on legacy until cutover)",
            default="",
        )

    def _progress(index: int, total: int, relative_path: str, bytes_copied: int) -> None:
        if total <= 0:
            return
        # Sparse progress: every item for small jobs, otherwise ~5% steps + last.
        step = max(1, total // 20)
        if index == 1 or index == total or index % step == 0:
            from mercury.terminal import screen as display_screen

            display_screen.write_hint(
                f"Progress {index}/{total}: {relative_path} ({bytes_copied} bytes so far)"
            )

    result = run_migration(
        execute=execute,
        confirmation=confirmation,
        update_state=update_state,
        write_repo_report=write_report,
        progress_callback=_progress if execute else None,
    )
    code = print_migration_run(result)
    if code:
        raise typer.Exit(code)


@storage_app.command("migrate-quarantine")
def storage_migrate_quarantine_cmd(
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Move primary conflict paths into .mercury_control/quarantine/ (default dry-run).",
    ),
    confirm: str | None = typer.Option(
        None,
        "--confirm",
        help="Confirmation phrase for --execute (QUARANTINE CONFLICTS).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Non-interactive confirm (still requires --confirm 'QUARANTINE CONFLICTS').",
    ),
) -> None:
    """Move conflicting primary paths aside (never deletes legacy USB; no overwrite)."""
    from mercury.storage.migrate_quarantine import (
        QUARANTINE_CONFIRMATION_PHRASE,
        quarantine_migration_conflicts,
    )
    from mercury.storage.terminal import print_quarantine_result

    confirmation = confirm
    if execute and confirmation is None:
        if yes:
            raise typer.BadParameter(
                f"--yes requires --confirm '{QUARANTINE_CONFIRMATION_PHRASE}'"
            )
        confirmation = typer.prompt(
            f"Type {QUARANTINE_CONFIRMATION_PHRASE} to move primary conflicts aside",
            default="",
        )
    result = quarantine_migration_conflicts(execute=execute, confirmation=confirmation)
    code = print_quarantine_result(result)
    if code:
        raise typer.Exit(code)


@storage_app.command("migrate-verify")
def storage_migrate_verify_cmd(
    update_state: bool = typer.Option(
        False,
        "--update-state",
        help="On success, set [storage].migration_state=verified in local.toml.",
    ),
    write_report: bool = typer.Option(
        False,
        "--write-report",
        help="Write JSON verify report under output/storage/.",
    ),
    record_generation: bool = typer.Option(
        False,
        "--record-generation",
        help="On successful verification, record this USB package generation as the final HDD mirror.",
    ),
) -> None:
    """Verify legacy content matches primary (no copy, no cutover)."""
    from mercury.storage.migrate_verify import verify_migration
    from mercury.storage.terminal import print_migration_verify

    report = verify_migration(update_state=update_state, write_repo_report=write_report)
    code = print_migration_verify(report)
    if report.ok and record_generation:
        from mercury.migration.generation import build_usb_generation, record_verified_generation

        path = record_verified_generation(build_usb_generation())
        output.write(f"Final package generation recorded: {path}")
    if code:
        raise typer.Exit(code)


@storage_app.command("cutover-readiness")
def storage_cutover_readiness_cmd() -> None:
    """Read-only checklist for future cutover (never switches writers or remounts)."""
    from mercury.storage.cutover_readiness import build_cutover_readiness
    from mercury.storage.terminal import print_cutover_readiness

    code = print_cutover_readiness(build_cutover_readiness())
    if code:
        raise typer.Exit(code)


@storage_app.command("cutover-approve")
def storage_cutover_approve_cmd(
    confirm: str = typer.Option("", "--confirm", help="Required phrase: USE HDD WRITER."),
) -> None:
    """Select verified HDD as writer; preserves a rollback config copy and never changes mounts."""
    from mercury.storage.cutover_approve import approve_hdd_writer_cutover

    try:
        backup = approve_hdd_writer_cutover(confirmation=confirm)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output.write(f"HDD is now the active writer. Rollback config: {backup}")


@storage_app.command("cutover-plan")
def storage_cutover_plan_cmd() -> None:
    """Preview every future USB→HDD writer-path change; never applies it."""
    from mercury.storage.cutover_plan import build_cutover_plan
    from mercury.storage.terminal import print_cutover_plan

    code = print_cutover_plan(build_cutover_plan())
    if code:
        raise typer.Exit(code)


@backup_app.command("plan")
def backup_plan(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Dry-run backup plan from platform demo catalog (required in seed).",
    ),
    sample_manifest: bool = typer.Option(
        False,
        "--sample-manifest",
        help="Write sample manifest JSON under output/samples/ (requires --demo).",
    ),
) -> None:
    """Build a dry-run full backup plan (no execution)."""
    from mercury.database import (
        MariaDbConfigError,
        MariaDbLiveError,
        build_discovered_backup_plan,
        discover,
        try_load_mariadb_config,
    )
    from mercury.database.backup_planning import build_backup_plan_from_inventory

    if demo:
        from mercury.database import build_demo_backup_plan

        plan = build_demo_backup_plan()
    elif try_load_mariadb_config() is not None:
        try:
            plan = build_backup_plan_from_inventory(discover("live"), live=True)
        except (MariaDbConfigError, MariaDbLiveError) as exc:
            typer.echo(f"Live discovery failed: {exc}")
            typer.echo("Falling back to config/catalog inventory.")
            plan = build_discovered_backup_plan()
    else:
        plan = build_discovered_backup_plan()

    from mercury.backup.terminal.plan import print_backup_plan

    print_backup_plan(plan, live=not demo and try_load_mariadb_config() is not None)

    if sample_manifest:
        if not demo:
            typer.echo("--sample-manifest requires --demo.")
            raise typer.Exit(1)
        from mercury.backup.sample_manifest import write_sample_manifests

        paths = write_sample_manifests()
        output.write()
        output.heading("Sample manifests written")
        for path in paths:
            output.item(str(path))


@backup_app.command("schema-plan")
def backup_schema_plan(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Schema-only export plan for backup sources (required in seed).",
    ),
) -> None:
    """Dry-run schema-only export plan (mariadb-dump --no-data, not executed)."""
    from mercury.database import MariaDbConfigError, MariaDbLiveError, try_load_mariadb_config
    from mercury.reporting.terminal.plan import print_schema_backup_plan
    from mercury.backup.schema_plan import (
        build_schema_backup_plan_demo,
        build_schema_backup_plan_live,
    )

    if demo:
        print_schema_backup_plan(build_schema_backup_plan_demo())
        return
    if try_load_mariadb_config() is None:
        typer.echo("No MariaDB config — use --demo or run: mercury config init")
        raise typer.Exit(1)
    try:
        print_schema_backup_plan(build_schema_backup_plan_live())
    except (MariaDbConfigError, MariaDbLiveError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


@backup_app.command("manifest-preview")
def backup_manifest_preview(
    db: str = typer.Option(..., "--db", help="Database name."),
    kind: str = typer.Option(
        ...,
        "--kind",
        help="Backup kind: full or schema_only.",
    ),
) -> None:
    """Print JSON manifest preview (dry-run; does not write files)."""
    from mercury.backup.manifest_preview import (
        ManifestPreviewError,
        build_manifest_preview,
        format_manifest_preview_json,
    )
    from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

    normalized = kind.strip().lower().replace("-", "_")
    if normalized not in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY):
        typer.echo("Invalid --kind. Use: full or schema_only")
        raise typer.Exit(1)

    try:
        preview = build_manifest_preview(db, normalized)  # type: ignore[arg-type]
    except ManifestPreviewError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    output.write(format_manifest_preview_json(preview))


@repo_app.command("status")
def repo_status_cmd(
    repo: list[str] = typer.Option(
        None,
        "--repo",
        help="Configured repo key or display name. Repeat to filter.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Show per-repository path and remote URL details.",
    ),
) -> None:
    """Inspect configured Git repositories without modifying them."""
    from mercury.repo import inspect_repositories, load_repo_definitions
    from mercury.repo.config import RepoSelectionError, select_repo_definitions
    from mercury.repo.terminal import print_repo_statuses

    try:
        definitions = select_repo_definitions(load_repo_definitions(), selected_keys=repo)
    except RepoSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    print_repo_statuses(inspect_repositories(definitions), verbose=verbose)


@repo_app.command("init-config")
def repo_init_config_cmd(
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite config/repos.toml when it already exists.",
    ),
) -> None:
    """Write config/repos.toml from the known local Fedora repo paths."""
    from mercury.repo import write_local_repo_config

    try:
        path, definitions = write_local_repo_config(force=force)
    except FileExistsError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    output.write(f"Wrote: {path}")
    output.write(f"Configured repositories: {len(definitions)}")
    if definitions:
        output.write("")
        for definition in definitions:
            output.write(f"- {definition.display_name}: {definition.path}")
    else:
        output.write("No known local repository paths were found.")


@repo_app.command("bundle")
def repo_bundle_cmd(
    repo: list[str] = typer.Option(
        None,
        "--repo",
        help="Configured repo key or display name. Repeat to filter.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Create Git bundles plus repo manifest and restore note on active operator storage.",
    ),
) -> None:
    """Plan or write Git bundles for configured repositories."""
    from mercury.repo import (
        build_repo_bundle_plan,
        execute_repo_bundle_plan,
        inspect_repositories,
        load_repo_bundle_settings,
        load_repo_definitions,
    )
    from mercury.repo.config import RepoSelectionError, select_repo_definitions
    from mercury.repo.terminal import print_repo_bundle_plan

    try:
        definitions = select_repo_definitions(load_repo_definitions(), selected_keys=repo)
    except RepoSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    plan = build_repo_bundle_plan(
        inspect_repositories(definitions),
        load_repo_bundle_settings(),
    )
    if not execute:
        print_repo_bundle_plan(plan, executed=False)
        return

    try:
        executed_plan = execute_repo_bundle_plan(plan)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    print_repo_bundle_plan(executed_plan, executed=True)


@repo_app.command("offline-sync")
def repo_offline_sync_cmd(
    execute: bool = typer.Option(False, "--execute", help="Create or update runnable HDD repository copies."),
    confirm: str = typer.Option("", "--confirm", help="Required with --execute: SYNC OFFLINE REPOS"),
) -> None:
    """Preview or synchronize independent offline Git worktrees on operator storage."""
    from mercury.repo import inspect_repositories, load_repo_definitions
    from mercury.repo.offline_clone import OFFLINE_SYNC_CONFIRMATION, build_offline_clone_plan, execute_offline_clone_plan
    from mercury.repo.offline_terminal import print_offline_clone_plan

    plan = build_offline_clone_plan(inspect_repositories(load_repo_definitions()))
    if not execute:
        print_offline_clone_plan(plan)
        return
    if confirm != OFFLINE_SYNC_CONFIRMATION:
        typer.echo(f"Refusing execution: pass --confirm '{OFFLINE_SYNC_CONFIRMATION}'.")
        raise typer.Exit(2)
    print_offline_clone_plan(execute_offline_clone_plan(plan), executed=True)


@transfer_app.command("status")
def transfer_status_cmd(
    live: bool = typer.Option(
        False,
        "--live",
        help="Use live database inventory and live sync readiness.",
    ),
    seed: bool = typer.Option(
        False,
        "--seed",
        help="Use catalog/seed inventory instead of live probes.",
    ),
) -> None:
    """Show one combined transfer summary for database and repository lanes."""
    from mercury.transfer import build_transfer_bundle, print_transfer_bundle
    from mercury.transfer.bundle import resolve_transfer_live

    print_transfer_bundle(build_transfer_bundle(live=resolve_transfer_live(live=live, seed=seed)))


@transfer_app.command("handoff")
def transfer_handoff_cmd(
    live: bool = typer.Option(
        False,
        "--live",
        help="Use live database inventory and live sync readiness.",
    ),
    seed: bool = typer.Option(
        False,
        "--seed",
        help="Use catalog/seed inventory instead of live probes.",
    ),
    run: bool = typer.Option(
        False,
        "--run",
        help="Run the guided handoff wizard instead of showing the checklist only.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="With --run, execute each wizard phase (default is dry-run planning).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="With --run, write partial bundles/manifests without freshness prompts.",
    ),
    from_phase: str | None = typer.Option(
        None,
        "--from-phase",
        help="With --run, start at one wizard phase: backup, verify, repo_bundle, db_bundle, transfer.",
    ),
    through_phase: str | None = typer.Option(
        None,
        "--through-phase",
        help="With --run, stop after one wizard phase: backup, verify, repo_bundle, db_bundle, transfer.",
    ),
) -> None:
    """Show workstation handoff readiness, or run the guided handoff wizard."""
    from mercury.handoff import build_handoff_checklist, print_handoff_checklist, run_guided_handoff_wizard
    from mercury.handoff.terminal import print_handoff_wizard_result
    from mercury.transfer.bundle import resolve_transfer_live

    use_live = resolve_transfer_live(live=live, seed=seed)
    if run:
        try:
            result = run_guided_handoff_wizard(
                live=use_live,
                execute=execute,
                force=force,
                start_phase=from_phase,
                end_phase=through_phase,
            )
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=2) from exc
        print_handoff_wizard_result(result)
        if execute and result.final_handoff_status not in {"complete", "complete with warnings"}:
            raise typer.Exit(code=1)
        return
    print_handoff_checklist(build_handoff_checklist(live=use_live))


@transfer_app.command("receive")
def transfer_receive_cmd(
    live: bool = typer.Option(
        False,
        "--live",
        help="Use live database inventory when contextualizing USB artifacts.",
    ),
    seed: bool = typer.Option(
        False,
        "--seed",
        help="Use catalog/seed inventory instead of live probes.",
    ),
) -> None:
    """Show the receiving-workstation guide for imported handoff media."""
    from mercury.handoff.receiver import build_receiver_handoff_guide
    from mercury.handoff.terminal import print_receiver_handoff_guide
    from mercury.transfer.bundle import resolve_transfer_live

    use_live = resolve_transfer_live(live=live, seed=seed)
    print_receiver_handoff_guide(checklist=build_receiver_handoff_guide(live=use_live))


@transfer_app.command("history")
def transfer_history_cmd(
    limit: int = typer.Option(
        12,
        "--limit",
        min=1,
        max=50,
        help="Maximum handoff history rows to show.",
    ),
) -> None:
    """Show recent handoff-related events from the USB state ledger."""
    from mercury.handoff.history import build_handoff_history
    from mercury.handoff.terminal import print_handoff_history

    print_handoff_history(build_handoff_history(limit=limit))


@transfer_app.command("write")
def transfer_write_cmd(
    live: bool = typer.Option(
        False,
        "--live",
        help="Use live database inventory and live sync readiness.",
    ),
    seed: bool = typer.Option(
        False,
        "--seed",
        help="Use catalog/seed inventory instead of live probes.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Write the combined transfer manifest and runbook to active operator storage.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Write even when handoff readiness is partial or has freshness warnings.",
    ),
) -> None:
    """Plan or write one combined transfer manifest and runbook."""
    from mercury.core.handoff_status import handoff_write_cli_error, handoff_write_requires_force
    from mercury.transfer import build_transfer_bundle, print_transfer_bundle, write_transfer_bundle
    from mercury.transfer.bundle import handoff_status_for_bundle, resolve_transfer_live

    use_live = resolve_transfer_live(live=live, seed=seed)
    bundle = build_transfer_bundle(live=use_live)
    if not execute:
        print_transfer_bundle(bundle, executed=False)
        return
    handoff_status = handoff_status_for_bundle(bundle)
    if handoff_write_requires_force(handoff_status) and not force:
        typer.echo(handoff_write_cli_error(handoff_status))
        raise typer.Exit(1)
    try:
        bundle = write_transfer_bundle(bundle)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    print_transfer_bundle(bundle, executed=True)


@backup_app.command("verify-plan")
def backup_verify_plan(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Show verification plan and demo preview results (required in seed).",
    ),
) -> None:
    """Dry-run backup verification plan (no files verified in seed)."""
    if not demo:
        typer.echo("Seed mode: use --demo for verification planning.")
        raise typer.Exit(1)
    from mercury.backup.verification import build_verification_plan_demo
    from mercury.backup.terminal.verify import print_verification_plan

    print_verification_plan(build_verification_plan_demo())


@backup_app.command("verify")
def backup_verify_cmd(
    db: str = typer.Option(..., "--db", help="Database name to verify."),
    path: str | None = typer.Option(
        None,
        "--path",
        help="Explicit backup directory (contains manifest.json).",
    ),
    latest: bool = typer.Option(
        True,
        "--latest/--no-latest",
        help="Use latest backup directory under backup_root (default).",
    ),
    update_manifest: bool = typer.Option(
        True,
        "--update-manifest/--no-update-manifest",
        help="Set manifest verified=true when verification passes (default).",
    ),
    allow_development_recovery: bool = typer.Option(
        False,
        "--allow-development-recovery",
        help="Allow only configured optional development recovery databases.",
    ),
) -> None:
    """Verify on-disk backup artifacts (manifest, dumps, checksums)."""
    from pathlib import Path

    from mercury.backup.find_latest_backup import find_latest_backup_directory
    from mercury.core.execution_policy import load_execution_policy
    from mercury.backup.verification import verify_backup_directory
    from mercury.backup.terminal.verify import print_verification_result

    backup_dir: Path | None = Path(path).expanduser() if path else None
    if backup_dir is None:
        if not latest:
            typer.echo("Provide --path or use --latest (default).")
            raise typer.Exit(1)
        policy = load_execution_policy()
        backup_dir = find_latest_backup_directory(policy.backup_root, db)
        if backup_dir is None:
            typer.echo(
                f"No backup directory with manifest.json found for '{db}' under {policy.backup_root}"
            )
            raise typer.Exit(1)

    result = verify_backup_directory(
        backup_dir,
        database=db,
        update_manifest=update_manifest,
        allow_development_backup=allow_development_recovery,
    )
    if result.database != db:
        typer.echo(
            f"Backup directory is for '{result.database}', not '{db}'. "
            "Use --path with the correct directory or fix --db."
        )
        raise typer.Exit(1)
    print_verification_result(result)
    if not result.verified:
        raise typer.Exit(1)


@backup_app.command("run")
def backup_run_cmd(
    db: str = typer.Option(..., "--db", help="Database name (backup source only)."),
    kind: str = typer.Option(
        ...,
        "--kind",
        help="Backup kind: full or schema_only.",
    ),
    execute: bool = typer.Option(
        True,
        "--execute/--dry-run",
        help="Execute backup (default). Use --dry-run to preview only.",
    ),
) -> None:
    """Execute a logical backup (use --dry-run to preview only)."""
    from mercury.backup.backup_runner import BackupExecutionError, execute_backup
    from mercury.backup.terminal.runner import print_backup_execution
    from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

    normalized = kind.strip().lower().replace("-", "_")
    if normalized not in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY):
        typer.echo("Invalid --kind. Use: full or schema_only")
        raise typer.Exit(1)

    try:
        result = execute_backup(db, normalized, execute=execute, live=True)  # type: ignore[arg-type]
    except BackupExecutionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    print_backup_execution(result)
    if result.refused and execute:
        raise typer.Exit(1)


@backup_app.command("batch")
def backup_batch_cmd(
    kind: str = typer.Option("full", "--kind", help="Backup kind: full or schema_only."),
    execute: bool = typer.Option(
        True,
        "--execute/--dry-run",
        help="Execute all backup sources (default). Use --dry-run to preview only.",
    ),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="After execute, verify newly written backup IDs (default: verify).",
    ),
    db: list[str] = typer.Option(
        None,
        "--db",
        help="Limit to one or more active backup source databases.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Use demo/catalog inventory instead of live server.",
    ),
) -> None:
    """Execute backups for all approved backup sources (use --dry-run to preview)."""
    from mercury.backup.batch_runner import (
        BackupSourceSelectionError,
        run_backup_batch,
        select_batch_sources,
        verify_written_backup_batch,
    )
    from mercury.backup.terminal.batch import print_backup_batch_result, print_batch_small_backup_warnings
    from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

    normalized = kind.strip().lower().replace("-", "_")
    if normalized not in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY):
        typer.echo("Invalid --kind. Use: full or schema_only")
        raise typer.Exit(1)

    try:
        sources = select_batch_sources(selected=db, live=not demo)
    except BackupSourceSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    batch = run_backup_batch(
        normalized,  # type: ignore[arg-type]
        execute=execute,
        live=not demo,
        sources=sources,
    )
    print_backup_batch_result(batch)
    if execute and batch.executed_count:
        print_batch_small_backup_warnings(batch)
    if execute and verify and batch.executed_count:
        verification = verify_written_backup_batch(batch)
        output.write(
            f"Batch verification: {verification.verified} verified · {verification.failed} failed"
        )
        for issue in verification.issues:
            output.write(f"  {issue}")
        if verification.failed:
            raise typer.Exit(1)
    if batch.errors or (execute and batch.refused_count and not batch.executed_count):
        raise typer.Exit(1)


@backup_app.command("dev")
def backup_dev_cmd(
    execute: bool = typer.Option(False, "--execute", help="Write optional development recovery backups."),
    confirm: str | None = typer.Option(None, "--confirm", help="Required with --execute: BACKUP DEV DATABASES."),
    demo: bool = typer.Option(False, "--demo", help="Use demo/catalog inventory instead of live server."),
) -> None:
    """Preview or explicitly back up configured development recovery targets."""
    from mercury.backup.batch_runner import (
        resolve_development_backup_sources, run_backup_batch, verify_development_backup_batch,
    )
    from mercury.backup.terminal.batch import print_backup_batch_result

    if execute and confirm != "BACKUP DEV DATABASES":
        raise typer.BadParameter("--execute requires --confirm 'BACKUP DEV DATABASES'")
    sources = resolve_development_backup_sources(live=not demo)
    if not sources:
        output.write("No configured development databases are present on this MariaDB server.")
        return
    batch = run_backup_batch(
        "full", execute=execute, live=not demo, sources=sources,
        allow_development_backup=True,
    )
    print_backup_batch_result(batch, databases_label="Development databases selected")
    if execute and batch.executed_count:
        verification = verify_development_backup_batch(batch)
        output.write(f"Development backup verification: {verification.verified} verified · {verification.failed} failed")
        for issue in verification.issues:
            output.write(f"  {issue}")
        if verification.failed:
            raise typer.Exit(1)
    if batch.errors or (execute and batch.refused_count and not batch.executed_count):
        raise typer.Exit(1)


@backup_app.command("full")
def backup_full_cmd(
    execute: bool = typer.Option(
        True,
        "--execute/--dry-run",
        help="Execute production full backup + verify (default). Use --dry-run to preview.",
    ),
    include_dev: bool = typer.Option(
        False,
        "--include-dev",
        help="Also back up configured development databases (requires --confirm-dev).",
    ),
    confirm_dev: str | None = typer.Option(
        None,
        "--confirm-dev",
        help="Required with --include-dev: BACKUP DEV DATABASES.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Use demo/catalog inventory instead of live server.",
    ),
) -> None:
    """Production write+verify full backup (CLI parity with Backup Operations [2])."""
    from datetime import datetime, timezone

    from mercury.backup.batch_runner import (
        BackupSourceSelectionError,
        FullBackupOutcome,
        build_full_backup_run_result,
        new_full_backup_run_id,
        resolve_development_backup_sources,
        run_backup_batch,
        select_batch_sources,
        verify_written_backup_batch,
        write_full_backup_run_receipt,
    )
    from mercury.backup.terminal.batch import print_backup_batch_result, print_full_backup_run_result
    from mercury.core.safety import BACKUP_KIND_FULL

    if include_dev and confirm_dev != "BACKUP DEV DATABASES":
        raise typer.BadParameter("--include-dev requires --confirm-dev 'BACKUP DEV DATABASES'")

    try:
        sources = select_batch_sources(live=not demo)
    except BackupSourceSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    started = datetime.now(timezone.utc)
    run_id = new_full_backup_run_id(now=started)
    production_batch = run_backup_batch(
        BACKUP_KIND_FULL,
        execute=execute,
        live=not demo,
        sources=sources,
    )
    print_backup_batch_result(
        production_batch,
        databases_label="Production databases selected",
    )

    production_verification = None
    if execute and production_batch.executed_count:
        production_verification = verify_written_backup_batch(production_batch)
        output.write(
            f"Production verification: {production_verification.verified} verified · "
            f"{production_verification.failed} failed"
        )
        for issue in production_verification.issues:
            output.write(f"  {issue}")

    development_batch = None
    development_verification = None
    if include_dev:
        dev_sources = resolve_development_backup_sources(live=not demo)
        if not dev_sources:
            output.write("No configured development databases are present on this MariaDB server.")
        else:
            development_batch = run_backup_batch(
                BACKUP_KIND_FULL,
                execute=execute,
                live=not demo,
                sources=dev_sources,
                allow_development_backup=True,
            )
            print_backup_batch_result(
                development_batch,
                databases_label="Development databases selected",
            )
            if execute and development_batch.executed_count:
                development_verification = verify_written_backup_batch(
                    development_batch, allow_development_backup=True
                )

    if not execute:
        if production_batch.errors or (
            production_batch.refused_count and not production_batch.executed_count
        ):
            raise typer.Exit(1)
        return

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
        result = result.model_copy(update={"receipt_path": str(receipt)})
    except Exception as exc:  # pragma: no cover - storage edge
        output.write(f"Warning: could not write full-backup run receipt: {exc}")
    print_full_backup_run_result(result)
    if result.outcome != FullBackupOutcome.PASS:
        raise typer.Exit(1)


@backup_app.command("all")
def backup_all_cmd(
    kind: str = typer.Option("full", "--kind", help="Backup kind: full or schema_only."),
    execute: bool = typer.Option(
        True,
        "--execute/--dry-run",
        help="Execute backups for active source databases (default). Use --dry-run to preview.",
    ),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="After execute, verify newly written backup IDs (default: verify).",
    ),
    db: list[str] = typer.Option(
        None,
        "--db",
        help="Limit to one or more active backup source databases.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Use demo/catalog inventory instead of live server.",
    ),
) -> None:
    """Alias for batch backup over the active source database set."""
    backup_batch_cmd(kind=kind, execute=execute, verify=verify, db=db, demo=demo)


@backup_app.command("verify-all")
def backup_verify_all_cmd(
    db: list[str] = typer.Option(
        None,
        "--db",
        help="Limit verification to one or more active backup source databases.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Use demo/catalog backup sources instead of live inventory.",
    ),
    update_manifest: bool = typer.Option(
        True,
        "--update-manifest/--no-update-manifest",
        help="Set manifest verified=true when verification passes (default).",
    ),
) -> None:
    """Verify latest on-disk backup for each backup source."""
    from mercury.backup.batch_runner import BackupSourceSelectionError, select_batch_sources
    from mercury.backup.find_latest_backup import find_latest_backup_directory
    from mercury.core.execution_policy import load_execution_policy
    from mercury.backup.verification import verify_backup_directory
    from mercury.backup.terminal.verify import print_verification_result

    policy = load_execution_policy()
    try:
        sources = select_batch_sources(selected=db, live=not demo)
    except BackupSourceSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    passed = 0
    failed = 0
    skipped = 0

    for db in sources:
        backup_dir = find_latest_backup_directory(policy.backup_root, db)
        if backup_dir is None:
            output.write(f"SKIP {db}: no backup directory under {policy.backup_root}")
            skipped += 1
            continue
        result = verify_backup_directory(
            backup_dir,
            database=db,
            update_manifest=update_manifest,
        )
        print_verification_result(result, compact=True)
        output.write("")
        if result.verified:
            passed += 1
        else:
            failed += 1

    output.heading("Verify-all summary")
    output.field("passed", passed)
    output.field("failed", failed)
    output.field("skipped", skipped)
    output.field("source databases checked", len(sources))

    if passed + failed + skipped == 0:
        typer.echo("No backup sources to verify.")
        raise typer.Exit(1)
    if passed + failed == 0:
        typer.echo("No on-disk backups found for any backup source.")
        raise typer.Exit(1)
    if failed or skipped:
        output.write()
        output.write(
            "Verify-all incomplete: "
            f"{passed} passed, {failed} failed, {skipped} missing backup(s)."
        )
        from mercury.logging.events import log_verify_all_summary

        log_verify_all_summary(passed=passed, failed=failed, skipped=skipped, sources=len(sources))
        raise typer.Exit(1)
    from mercury.logging.events import log_verify_all_summary

    log_verify_all_summary(passed=passed, failed=failed, skipped=skipped, sources=len(sources))
    output.write()
    output.write("Verify-all complete: all backup sources passed verification.")


@backup_app.command("status")
def backup_status_cmd(
    db: list[str] = typer.Option(
        None,
        "--db",
        help="Limit status to one or more active backup source databases.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Use demo/catalog backup sources instead of live inventory.",
    ),
) -> None:
    """Show latest protection status for active source database backups."""
    from mercury.backup import build_backup_status_report, print_backup_status_report
    from mercury.backup.batch_runner import BackupSourceSelectionError

    try:
        report = build_backup_status_report(live=not demo, selected=db)
    except BackupSourceSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    print_backup_status_report(report)


@backup_app.command("bundle")
def backup_bundle_cmd(
    db: list[str] = typer.Option(
        None,
        "--db",
        help="Limit bundle output to one or more active backup source databases.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Use demo/catalog backup sources instead of live inventory.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Write database manifest and restore runbook files to active operator storage.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Write even when handoff package is partial or has freshness warnings.",
    ),
) -> None:
    """Plan or write one database transfer manifest/runbook set for active source backups."""
    from mercury.backup import (
        build_database_bundle_plan,
        print_database_bundle_plan,
        write_database_bundle_plan,
    )
    from mercury.backup.batch_runner import BackupSourceSelectionError
    from mercury.backup.bundle import bundle_package_status
    from mercury.core.handoff_status import handoff_write_cli_error, handoff_write_requires_force

    try:
        plan = build_database_bundle_plan(live=not demo, selected=db)
    except BackupSourceSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    if not execute:
        print_database_bundle_plan(plan, executed=False)
        return
    package_status = bundle_package_status(plan)
    if handoff_write_requires_force(package_status) and not force:
        typer.echo(handoff_write_cli_error(package_status))
        raise typer.Exit(1)
    try:
        write_database_bundle_plan(plan)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    output.write()
    print_database_bundle_plan(plan, executed=True)


@backup_app.command("list")
def backup_list_cmd(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="List demo planned backup records from manifest previews.",
    ),
) -> None:
    """List backup records (on-disk when available, else demo preview)."""
    from mercury.core.execution_policy import load_execution_policy

    if demo:
        from mercury.backup.on_disk_index import build_demo_backup_list
        from mercury.backup.terminal.verify import print_demo_backup_list

        print_demo_backup_list(build_demo_backup_list())
        return

    from mercury.backup.on_disk_index import build_on_disk_backup_list
    from mercury.backup.terminal.verify import print_on_disk_backup_list

    policy = load_execution_policy()
    backup_list = build_on_disk_backup_list(policy.backup_root)
    if not backup_list.records:
        typer.echo("No on-disk backups found.")
        typer.echo(f"  backup_root: {policy.backup_root}")
        typer.echo("Use --demo for planned-record preview, or run: mercury backup run --db <prod> --kind full")
        raise typer.Exit(1)
    print_on_disk_backup_list(backup_list)


@report_app.command("preview")
def report_preview_cmd(
    db: str = typer.Option(..., "--db", help="Database name."),
    kind: str = typer.Option(..., "--kind", help="full or schema_only"),
) -> None:
    """Markdown-style backup report preview (dry-run; no file write)."""
    from mercury.backup.manifest_preview import ManifestPreviewError
    from mercury.reporting.preview import build_report_preview, format_report_preview_markdown
    from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

    normalized = kind.strip().lower().replace("-", "_")
    if normalized not in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY):
        typer.echo("Invalid --kind. Use: full or schema_only")
        raise typer.Exit(1)
    try:
        report = build_report_preview(db, normalized)  # type: ignore[arg-type]
    except ManifestPreviewError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    output.write(format_report_preview_markdown(report))


@sync_app.command("plan")
def sync_plan_cmd(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Production sync-pair plan from platform catalog (required in seed).",
    ),
) -> None:
    """Dry-run production sync-pair plan with prerequisites (not executed)."""
    from mercury.database import MariaDbConfigError, MariaDbLiveError, try_load_mariadb_config
    from mercury.reporting.terminal.plan import print_sync_plan
    from mercury.sync.sync_plan import build_sync_plan_demo, build_sync_plan_live

    if demo:
        print_sync_plan(build_sync_plan_demo())
        return
    if try_load_mariadb_config() is None:
        typer.echo("No MariaDB config — use --demo or run: mercury config init")
        raise typer.Exit(1)
    try:
        print_sync_plan(build_sync_plan_live())
    except (MariaDbConfigError, MariaDbLiveError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


@sync_app.command("readiness")
def sync_readiness_cmd(
    live: bool = typer.Option(
        True,
        "--live/--demo",
        help="Use live server inventory and freshness probes (default) or demo/catalog only.",
    ),
) -> None:
    """Report which production sync pairs have artifact-verified fresh full backups."""
    from mercury.database import MariaDbConfigError, MariaDbLiveError
    from mercury.sync.readiness import build_sync_readiness_report
    from mercury.sync.terminal.readiness import print_sync_readiness_report

    try:
        print_sync_readiness_report(build_sync_readiness_report(live=live), compact=True)
    except (MariaDbConfigError, MariaDbLiveError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


@sync_app.command("run")
def sync_run_cmd(
    live: bool = typer.Option(True, "--live/--demo", help="Use live inventory."),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Restore verified backups into dev targets (requires live actions and typing SYNC DEV).",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Limit sync to one production source database.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Limit sync to one development target database.",
    ),
) -> None:
    """Plan or execute development refresh for ready production sync pairs."""
    from mercury.core.execution_policy import load_execution_policy
    from mercury.core.safety import SYNC_DEV_CONFIRMATION_PHRASE
    from mercury.sync.sync_runner import run_sync_batch
    from mercury.sync.selection import select_sync_entries
    from mercury.sync.terminal.runner import print_sync_batch_result
    from mercury.sync.readiness import build_sync_readiness_report

    report = build_sync_readiness_report(live=live)
    ready = [entry for entry in report.entries if entry.ready_for_sync_planning]
    ready = select_sync_entries(ready, source=source, target=target)
    if not ready:
        if source or target:
            typer.echo("No ready production sync pairs matched the requested source/target filter.")
        else:
            typer.echo("No ready production sync pairs. Run: mercury sync readiness --live")
        raise typer.Exit(1)

    policy = load_execution_policy()
    if execute and policy.live_execution_allowed():
        typer.echo(f"Type {SYNC_DEV_CONFIRMATION_PHRASE!r} to sync into dev.")
        typed = typer.prompt("Confirm")
        if typed != SYNC_DEV_CONFIRMATION_PHRASE:
            typer.echo("Cancelled.")
            raise typer.Exit(1)

    batch = run_sync_batch(ready, execute=execute, policy=policy)
    print_sync_batch_result(batch, compact=True)
    if execute and batch.executed_count == 0:
        raise typer.Exit(1)


@sync_app.command("all")
def sync_all_cmd(
    live: bool = typer.Option(True, "--live/--demo", help="Use live inventory."),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Restore all ready verified backups into dev targets (requires live actions and typing SYNC DEV).",
    ),
) -> None:
    """Plan or execute sync for all ready production sync pairs."""
    sync_run_cmd(live=live, execute=execute, source=None, target=None)


@sync_app.command("verify")
def sync_verify_cmd(
    live: bool = typer.Option(True, "--live/--demo", help="Use live inventory."),
) -> None:
    """Read-only verification of dev targets against verified prod backup baselines."""
    from mercury.sync.verification import build_sync_verification_report
    from mercury.sync.terminal.verification import print_sync_verification_report

    print_sync_verification_report(build_sync_verification_report(live=live), compact=True)


@deploy_app.command("db")
def deploy_db_cmd(
    database: list[str] | None = typer.Option(
        None,
        "--database",
        "--db",
        help="Deploy one protected database (repeatable).",
    ),
    all_sources: bool = typer.Option(
        False,
        "--all",
        help="Deploy all protected databases with verified backups.",
    ),
    latest: bool = typer.Option(
        True,
        "--latest/--no-latest",
        help="Use the latest verified backup per database (default).",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Plan only; do not import (default).",
    ),
    plan_only: bool = typer.Option(
        False,
        "--plan-only",
        help="Alias for dry-run plan output.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Execute deployment (requires live actions enabled).",
    ),
    allow_create: bool = typer.Option(
        True,
        "--allow-create/--no-allow-create",
        help="Allow CREATE DATABASE when target is missing.",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        help="Skip databases that already exist locally (default).",
    ),
) -> None:
    """Deploy verified operator-storage backups onto this MariaDB host (dry-run by default)."""
    from mercury.deploy.models import DeployOptions
    from mercury.deploy.plan import build_deployment_plan
    from mercury.deploy.runner import execute_deployment_batch
    from mercury.deploy.terminal.plan import print_deployment_plan
    from mercury.deploy.terminal.summary import print_deployment_summary

    if plan_only:
        dry_run = True
        execute = False
    if execute:
        dry_run = False

    selected: list[str] | None = list(database) if database else None
    if all_sources:
        selected = None
    elif not selected and not latest:
        typer.echo("Specify --all, --database, or use --latest (default).")
        raise typer.Exit(1)

    options = DeployOptions(
        allow_create_database=allow_create,
        skip_existing=skip_existing,
    )
    if dry_run and not execute:
        plan = build_deployment_plan(databases=selected, options=options, execute=False)
        print_deployment_plan(plan)
        if not plan.candidates and plan.blockers:
            raise typer.Exit(1)
        return

    batch = execute_deployment_batch(databases=selected, options=options, execute=True)
    print_deployment_summary(batch)
    if batch.failed_count:
        raise typer.Exit(1)


@deploy_app.command("dev")
def deploy_dev_cmd(
    database: list[str] | None = typer.Option(
        None,
        "--database",
        "--db",
        help="Deploy one configured development recovery database (repeatable).",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Plan only; do not import (default).",
    ),
    execute: bool = typer.Option(False, "--execute", help="Import the selected development backups."),
    confirm: str | None = typer.Option(
        None,
        "--confirm",
        help="Required with --execute: DEPLOY DEV BACKUPS.",
    ),
    allow_create: bool = typer.Option(
        True,
        "--allow-create/--no-allow-create",
        help="Allow CREATE DATABASE when a development target is missing.",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        help="Skip development databases that already exist locally (default).",
    ),
) -> None:
    """Plan or explicitly deploy verified HDD development recovery backups."""
    from mercury.backup.batch_runner import resolve_development_backup_sources
    from mercury.deploy.models import DeployOptions
    from mercury.deploy.plan import build_deployment_plan
    from mercury.deploy.runner import execute_deployment_batch
    from mercury.deploy.terminal.plan import print_deployment_plan
    from mercury.deploy.terminal.summary import print_deployment_summary

    allowed = set(resolve_development_backup_sources(live=False))
    selected = list(database) if database else None
    if selected and (invalid := sorted(set(selected) - allowed)):
        typer.echo("Development deployment only permits configured targets: " + ", ".join(sorted(allowed)))
        raise typer.Exit(1)
    if execute and confirm != "DEPLOY DEV BACKUPS":
        raise typer.BadParameter("--execute requires --confirm 'DEPLOY DEV BACKUPS'")
    if execute:
        dry_run = False

    options = DeployOptions(
        allow_create_database=allow_create,
        skip_existing=skip_existing,
    )
    if dry_run and not execute:
        plan = build_deployment_plan(
            databases=selected,
            options=options,
            execute=False,
            allow_development_deploy=True,
        )
        print_deployment_plan(plan)
        if not plan.candidates and plan.blockers:
            raise typer.Exit(1)
        return

    batch = execute_deployment_batch(
        databases=selected,
        options=options,
        execute=True,
        allow_development_deploy=True,
    )
    print_deployment_summary(batch)
    if batch.failed_count:
        raise typer.Exit(1)


@deploy_app.command("repos")
def deploy_repos_cmd(
    repo: list[str] | None = typer.Option(
        None,
        "--repo",
        help="Configured repo key or display name. Repeat to filter.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Plan only; do not clone (default).",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Execute repository deployment (requires live actions enabled).",
    ),
    from_github: bool = typer.Option(
        False,
        "--from-github",
        help="Clone missing repositories from configured remote_url values.",
    ),
    from_usb: bool = typer.Option(
        False,
        "--from-usb",
        help="Clone missing repositories from verified operator-storage git bundles.",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        help="Skip repositories that already exist locally (default).",
    ),
) -> None:
    """Deploy configured Git repositories onto this workstation (dry-run by default)."""
    from mercury.deploy.repos.build_plan import build_repo_deploy_plan
    from mercury.deploy.repos.models import RepoDeployOptions
    from mercury.deploy.repos.runner import execute_repo_deploy_batch
    from mercury.deploy.repos.terminal.plan import print_repo_deploy_plan
    from mercury.deploy.repos.terminal.summary import print_repo_deploy_summary

    if execute:
        dry_run = False
    source_mode = "auto"
    if from_github:
        source_mode = "github"
    elif from_usb:
        source_mode = "usb"

    options = RepoDeployOptions(skip_existing=skip_existing)
    selected = list(repo) if repo else None
    if dry_run and not execute:
        print_repo_deploy_plan(
            build_repo_deploy_plan(
                selected_keys=selected,
                options=options,
                source_mode=source_mode,
                execute=False,
            )
        )
        return

    batch = execute_repo_deploy_batch(
        selected_keys=selected,
        options=options,
        source_mode=source_mode,
        execute=True,
    )
    print_repo_deploy_summary(batch)
    if batch.failed_count:
        raise typer.Exit(1)


@deploy_app.command("system")
def deploy_system_cmd(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Show combined database + repository plan (default).",
    ),
) -> None:
    """Dry-run plan for databases and repositories on a fresh workstation."""
    from mercury.deploy.system import build_system_deploy_plan, print_system_deploy_plan

    if dry_run:
        print_system_deploy_plan(build_system_deploy_plan(execute=False))
        return
    typer.echo("Live combined system deploy is not automated yet. Run deploy db and deploy repos separately.")
    raise typer.Exit(1)


@deploy_app.command("use-cases")
def deploy_use_cases_cmd() -> None:
    """Show deployment scenarios detected for this workstation."""
    from mercury.deploy.terminal.use_cases import print_deploy_use_cases
    from mercury.deploy.use_cases import detect_deploy_use_cases

    print_deploy_use_cases(detect_deploy_use_cases())


@deploy_app.command("status")
def deploy_status_cmd(
    check_db: bool = typer.Option(
        True,
        "--check-db/--no-check-db",
        help="Probe MariaDB and repository state on this host.",
    ),
) -> None:
    """Post-rebuild checkpoint: databases, repositories, USB, sync, and next step."""
    from mercury.deploy.rebuild_status import build_rebuild_status_report
    from mercury.deploy.terminal.rebuild_status import print_rebuild_status

    print_rebuild_status(build_rebuild_status_report(probe_database=check_db))


@restore_app.command("readiness")
def restore_check_readiness_cmd(
    db: list[str] = typer.Option(
        None,
        "--db",
        help="Limit to one or more production backup source databases.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Use demo/catalog backup sources instead of live inventory.",
    ),
) -> None:
    """Read-only deployment check: target/schema completeness vs backup baseline (not data freshness)."""
    from mercury.backup.batch_runner import BackupSourceSelectionError, select_batch_sources
    from mercury.core.runtime import should_probe_database_status
    from mercury.restore.readiness import build_target_completeness_report, restore_readiness_should_fail
    from mercury.restore.terminal.readiness import print_target_completeness_report

    probe = should_probe_database_status() and not demo
    try:
        databases = select_batch_sources(selected=db, live=probe) if db else None
    except BackupSourceSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    report = build_target_completeness_report(databases=databases, live=not demo)
    print_target_completeness_report(report)
    if restore_readiness_should_fail(report, live=not demo):
        raise typer.Exit(1)


@restore_app.command("plan")
def restore_check_plan_cmd(
    db: str = typer.Option(..., "--db", help="Production database to restore-check."),
) -> None:
    """Dry-run plan to restore latest verified backup into _restorecheck_* (not executed)."""
    from mercury.restore.check_plan import build_restore_check_plan
    from mercury.restore.terminal.check import print_restore_check_plan

    plan = build_restore_check_plan(db)
    print_restore_check_plan(plan)
    from mercury.logging.events import log_restore_check

    log_restore_check(database=db, allowed=plan.allowed, blocker_count=len(plan.blockers))
    if not plan.allowed:
        raise typer.Exit(1)


@restore_app.command("run")
def restore_check_run_cmd(
    db: str = typer.Option(..., "--db", help="Production database to restore-check."),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Execute restore into _restorecheck_* (requires live actions).",
    ),
) -> None:
    """Plan or execute restore-check into a temporary _restorecheck_* database."""
    from pathlib import Path

    from mercury.backup.backup_runner import BackupExecutionError
    from mercury.restore.check_plan import build_restore_check_plan
    from mercury.restore.terminal.check import print_restore_check_plan
    from mercury.restore.restore_runner import execute_restore_into_database
    from mercury.restore.terminal.runner import print_restore_execution_result

    plan = build_restore_check_plan(db)
    if not execute:
        print_restore_check_plan(plan)
        if not plan.allowed:
            raise typer.Exit(1)
        return

    if not plan.allowed or not plan.dump_file or not plan.backup_directory:
        print_restore_check_plan(plan)
        raise typer.Exit(1)

    dump_path = Path(plan.backup_directory) / plan.dump_file
    try:
        result = execute_restore_into_database(
            target_database=plan.restore_target,
            dump_path=dump_path,
            source_database=plan.source_prod,
            execute=True,
            cleanup_after_success=True,
        )
    except BackupExecutionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    print_restore_execution_result(result)
    if not result.executed or result.verification_passed is False:
        raise typer.Exit(1)


@restore_app.command("cleanup")
def restore_check_cleanup_cmd(
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Drop all _restorecheck_* databases (requires live actions).",
    ),
) -> None:
    """List or drop temporary restore-check databases on the server."""
    from mercury.core.runtime import should_probe_database_status
    from mercury.database import MariaDbConfigError, MariaDbLiveError, discover
    from mercury.restore.check_cleanup import cleanup_restorecheck_databases
    from mercury.restore.terminal.check_cleanup import print_restorecheck_cleanup_batch

    if not should_probe_database_status():
        typer.echo("MariaDB not configured — run: mercury config init")
        raise typer.Exit(1)

    try:
        inventory = discover("live")
    except (MariaDbConfigError, MariaDbLiveError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    names = [entry.name for entry in inventory.entries]
    batch = cleanup_restorecheck_databases(names, execute=execute)
    print_restorecheck_cleanup_batch(batch, compact=True)
    if execute and batch.databases and batch.dropped_count == 0:
        raise typer.Exit(1)


@config_app.command("validate")
def config_validate(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Validate demo/catalog inventory instead of config files only.",
    ),
) -> None:
    """Validate database names against Mercury backup policy."""
    from mercury.database import validate_config_policy
    from mercury.database import print_policy_report

    report = validate_config_policy(use_demo_catalog=demo)
    print_policy_report(report)
    if not report.ok():
        raise typer.Exit(1)


@app.command("status")
def status_cmd(
    save: bool = typer.Option(
        False,
        "--save",
        help="Write report to output/protection_status.txt",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Use live read-only server inventory (requires config/local.toml).",
    ),
) -> None:
    """Protection snapshot: backup sources, gaps, prod→dev pairs, action items."""
    from mercury.database import MariaDbConfigError, MariaDbLiveError
    from mercury.core.paths import OUTPUT_DIR, PROTECTION_REPORT_FILE
    from mercury.reporting.protection import build_protection_report, format_protection_report

    try:
        report = build_protection_report(live=live, probe_database=live)
    except MariaDbConfigError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    except MariaDbLiveError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    text = format_protection_report(report, compact=not save)
    output.write(text)

    if save:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        PROTECTION_REPORT_FILE.write_text(text + "\n", encoding="utf-8")
        output.write()
        output.write(f"Saved: {PROTECTION_REPORT_FILE}")


@config_app.command("init")
def config_init_cmd(
    force: bool = typer.Option(False, "--force", help="Overwrite existing local config."),
) -> None:
    """Create config/databases.toml, config/repos.toml, and config/local.toml from examples."""
    from mercury.config.init import init_local_config

    output.heading("Initialize local config")
    for line in init_local_config(force=force):
        output.item(line)


@config_app.command("repair-local")
def config_repair_local_cmd() -> None:
    """Add missing operator-storage artifact paths to config/local.toml without overwriting settings."""
    from mercury.config.init import repair_local_config_paths

    output.heading("Repair local config")
    for line in repair_local_config_paths():
        output.item(line)


@repair_app.command("usb")
def repair_usb_cmd(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Run the sudo USB repair script (mount, layout, ownership, enable boot mount).",
    ),
) -> None:
    """Describe or apply one-shot Mercury USB mount and ownership repair."""
    from mercury.repair.usb import USB_REPAIR_COMMAND, describe_usb_repair

    output.heading("Mercury USB repair")
    for line in describe_usb_repair():
        output.item(line)
    try:
        from mercury.core.storage_roots import load_storage_config
        from mercury.core.storage_roles import StorageWriteRole

        cfg = load_storage_config(warn_deprecated=False)
        if cfg.cutover_complete and cfg.active_write_role == StorageWriteRole.PRIMARY:
            output.write("")
            output.item(
                "Note: HDD is the active writer after cutover — USB repair is optional archive maintenance."
            )
            output.item("For routine operator storage: ./run.sh storage validate")
    except Exception:
        pass
    output.write("")
    output.item(f"Command: {USB_REPAIR_COMMAND}")
    if not apply:
        output.write("")
        output.item("Review scripts/repair-mercury-usb.sh, then run the command above.")
        return

    from mercury.repair.startup import run_usb_repair_flow

    if not run_usb_repair_flow(interactive=False):
        raise typer.Exit(1)


@app.command("doctor")
def doctor_cmd(
    repair_plan: bool = typer.Option(
        False,
        "--repair-plan",
        help="Print grouped repair commands (never executed automatically).",
    ),
    rebuild_summary: bool = typer.Option(
        False,
        "--rebuild-summary",
        help="Show post-rebuild checkpoint (databases, repos, USB, next step).",
    ),
    self_heal: bool = typer.Option(
        False,
        "--self-heal",
        help="Create missing Mercury directories when parent paths are writable (no sudo).",
    ),
    check_db: bool = typer.Option(
        True,
        "--check-db/--no-check-db",
        help="Probe configured MariaDB credentials when config is present.",
    ),
) -> None:
    """Diagnose fresh-rebuild setup, permissions, and MariaDB readiness."""
    from mercury.env.doctor import run_doctor
    from mercury.env.terminal.doctor import print_doctor_report, print_repair_plan

    if rebuild_summary:
        from mercury.deploy.rebuild_status import build_rebuild_status_report
        from mercury.deploy.terminal.rebuild_status import print_rebuild_status

        print_rebuild_status(build_rebuild_status_report(probe_database=check_db))
        return

    report = run_doctor(probe_database=check_db, self_heal=self_heal)
    if repair_plan:
        print_repair_plan(report)
    else:
        print_doctor_report(report)
    if report.blockers:
        raise typer.Exit(1)


@app.command("menu")
def menu_cmd() -> None:
    """Open the Mercury interactive menu."""
    from mercury.menu.runners import run_menu

    run_menu(interactive=True)


@logs_app.command("path")
def logs_path_cmd() -> None:
    """Show active log file paths."""
    from mercury.logging import (
        configure_logging,
        current_backup_log_file,
        current_database_log_file,
        current_error_log_file,
        current_log_file,
        resolve_log_dir,
    )
    from mercury.core.paths import LOGS_DIR
    from mercury.terminal import screen as display_screen

    configure_logging()
    display_screen.write_fields(
        {
            "log_dir": resolve_log_dir(),
            "default_log_dir": LOGS_DIR,
            "main_log": current_log_file() or "(logging disabled)",
            "error_log": current_error_log_file() or "(logging disabled)",
            "database_log": current_database_log_file() or "(logging disabled)",
            "backup_log": current_backup_log_file() or "(logging disabled)",
        }
    )


@logs_app.command("status")
def logs_status_cmd() -> None:
    """Summary of log files, error counts, and recent sessions."""
    from mercury.logging.analysis import build_log_status
    from mercury.logging import configure_logging
    from mercury.logging.terminal.status import print_log_status

    configure_logging()
    print_log_status(build_log_status())


@logs_app.command("sessions")
def logs_sessions_cmd(
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50, help="Number of sessions to show."),
) -> None:
    """List recent Mercury CLI/menu sessions from log files."""
    from mercury.logging.analysis import parse_recent_sessions
    from mercury.logging import configure_logging, resolve_log_dir
    from mercury.terminal import screen as display_screen

    configure_logging()
    sessions = parse_recent_sessions(max_sessions=limit)
    display_screen.write_section("Recent sessions")
    display_screen.write_fields({"log_dir": resolve_log_dir(), "count": len(sessions)})
    if not sessions:
        display_screen.write_status("warn", "No sessions found yet.")
        return
    rows = []
    for session in sessions:
        command = session.command or "(unknown)"
        if len(command) > 52:
            command = f"…{command[-51:]}"
        rows.append([session.session_id, command, "" if session.exit_code is None else str(session.exit_code)])
    display_screen.write_table(["SESSION", "COMMAND", "EXIT"], rows)


@logs_app.command("errors")
def logs_errors_cmd(
    lines: int = typer.Option(30, "--lines", "-n", min=1, max=500, help="Lines to show from error.log."),
) -> None:
    """Show recent errors from error.log."""
    from mercury.logging.analysis import analyze_log_file
    from mercury.logging import configure_logging, current_error_log_file, read_log_tail, resolve_log_dir
    from mercury.terminal import screen as display_screen

    configure_logging()
    error_file = current_error_log_file() or resolve_log_dir() / "error.log"
    display_screen.write_section("Recent errors")
    if error_file.is_file():
        info = analyze_log_file(error_file)
        display_screen.write_fields({"file": error_file, "errors": info.errors, "lines": info.lines})
    else:
        display_screen.write_fields({"file": error_file})
    tail = read_log_tail(lines=lines, log_file=error_file if error_file.is_file() else None)
    if not tail:
        display_screen.write_status("ok", "No errors logged.")
        return
    for line in tail:
        output.write(line)


@logs_app.command("list")
def logs_list_cmd() -> None:
    """List Mercury log files newest first."""
    from mercury.logging.analysis import analyze_log_file
    from mercury.logging import list_all_log_files, resolve_log_dir
    from mercury.terminal import format as display_format
    from mercury.terminal import screen as display_screen

    files = list_all_log_files()
    display_screen.write_section("Mercury logs")
    display_screen.write_fields({"log_dir": resolve_log_dir(), "count": len(files)})
    if not files:
        display_screen.write_status("warn", "No log files yet — run any mercury command.")
        return
    rows = []
    for path in files:
        info = analyze_log_file(path)
        rows.append([path.name, display_format.format_bytes(info.size_bytes), str(info.errors)])
    display_screen.write_table(["FILE", "SIZE", "ERRORS"], rows)


@logs_app.command("tail")
def logs_tail_cmd(
    lines: int = typer.Option(50, "--lines", "-n", min=1, max=500, help="Lines to show."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Log file name, path, or shorthand."),
    errors: bool = typer.Option(False, "--errors", help="Tail error.log."),
    database: bool = typer.Option(False, "--database", help="Tail database.log."),
    backup: bool = typer.Option(False, "--backup", help="Tail backup.log."),
    main: bool = typer.Option(False, "--main", help="Tail latest main daily log."),
) -> None:
    """Show the tail of a log file."""
    from mercury.logging.analysis import resolve_named_log_file
    from mercury.logging import list_all_log_files, read_log_tail, resolve_log_dir
    from mercury.terminal import screen as display_screen

    log_file: Path | None = None
    if errors:
        log_file = resolve_named_log_file("errors")
    elif database:
        log_file = resolve_named_log_file("database")
    elif backup:
        log_file = resolve_named_log_file("backup")
    elif main:
        log_file = resolve_named_log_file("main")
    elif file:
        log_file = resolve_named_log_file(file)

    tail = read_log_tail(lines=lines, log_file=log_file)
    display_screen.write_section("Log tail")
    if log_file:
        display_screen.write_fields({"file": log_file})
    elif list_all_log_files():
        display_screen.write_fields({"file": list_all_log_files()[0]})
    if not tail:
        display_screen.write_status("warn", "No log content available.")
        return
    for line in tail:
        output.write(line)


@logs_app.command("search")
def logs_search_cmd(
    pattern: str = typer.Argument(..., help="Text to search for in recent log files."),
    max_matches: int = typer.Option(50, "--max", "-m", min=1, max=500, help="Maximum matches."),
    ignore_case: bool = typer.Option(True, "--ignore-case/--case-sensitive", help="Case-insensitive search."),
    in_file: Optional[str] = typer.Option(
        None,
        "--in",
        help="Limit search to: errors, database, backup, main, or a filename.",
    ),
) -> None:
    """Search Mercury log files for a pattern."""
    from mercury.logging.analysis import resolve_named_log_file
    from mercury.logging import list_all_log_files, resolve_log_dir, search_log_files
    from mercury.terminal import screen as display_screen

    log_dir = resolve_log_dir()
    if in_file:
        target = resolve_named_log_file(in_file, log_dir=log_dir)
        paths = [target] if target and target.is_file() else []
        matches = []
        import re

        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(re.escape(pattern), flags)
        for path in paths:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                if regex.search(line):
                    matches.append((path, line_number, line))
                    if len(matches) >= max_matches:
                        break
    else:
        matches = search_log_files(pattern, max_matches=max_matches, ignore_case=ignore_case, log_dir=log_dir)

    display_screen.write_section("Log search")
    display_screen.write_fields({"pattern": pattern, "matches": len(matches)})
    if not matches:
        display_screen.write_status("warn", "No matches found.")
        return
    rows = [[match[0].name, str(match[1]), match[2][:120]] for match in matches]
    display_screen.write_table(["FILE", "LINE", "TEXT"], rows)


def main() -> None:
    from mercury.bootstrap import prepare_for_argv, run_with_session_logging

    prepare_for_argv(db_app, database_app)
    run_with_session_logging(app)


if __name__ == "__main__":
    main()
