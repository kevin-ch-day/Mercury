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
report_app = typer.Typer(help="Backup report previews (dry-run).")
logs_app = typer.Typer(help="Mercury log files under logs/.")

app.add_typer(env_app, name="env")
app.add_typer(db_app, name="db")
app.add_typer(database_app, name="database")
app.add_typer(backup_app, name="backup")
app.add_typer(repo_app, name="repo")
app.add_typer(transfer_app, name="transfer")
app.add_typer(config_app, name="config")
app.add_typer(sync_app, name="sync")
app.add_typer(restore_app, name="restore-check")
app.add_typer(report_app, name="report")
app.add_typer(logs_app, name="logs")


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
            plan = build_backup_plan_from_inventory(discover("live"))
        except (MariaDbConfigError, MariaDbLiveError) as exc:
            typer.echo(f"Live discovery failed: {exc}")
            typer.echo("Falling back to config/catalog inventory.")
            plan = build_discovered_backup_plan()
    else:
        plan = build_discovered_backup_plan()

    from mercury.backup.terminal.plan import print_backup_plan

    print_backup_plan(plan)

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
        help="Create Git bundles plus repo manifest and restore note on the USB target.",
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


@transfer_app.command("status")
def transfer_status_cmd(
    live: bool = typer.Option(
        False,
        "--live",
        help="Use live database inventory and live sync readiness.",
    ),
) -> None:
    """Show one combined transfer summary for database and repository lanes."""
    from mercury.transfer import build_transfer_bundle, print_transfer_bundle

    print_transfer_bundle(build_transfer_bundle(live=live))


@transfer_app.command("write")
def transfer_write_cmd(
    live: bool = typer.Option(
        False,
        "--live",
        help="Use live database inventory and live sync readiness.",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Write the combined transfer manifest and runbook to the USB target.",
    ),
) -> None:
    """Plan or write one combined transfer manifest and runbook."""
    from mercury.transfer import build_transfer_bundle, print_transfer_bundle, write_transfer_bundle

    bundle = build_transfer_bundle(live=live)
    print_transfer_bundle(bundle)
    if not execute:
        return
    try:
        write_transfer_bundle(bundle)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    output.write()
    output.write(f"Wrote: {bundle.transfer_manifest_path}")
    output.write(f"Wrote: {bundle.transfer_runbook_path}")


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
        False,
        "--update-manifest",
        help="Set manifest verified=true when verification passes.",
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

    result = verify_backup_directory(backup_dir, database=db, update_manifest=update_manifest)
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
        False,
        "--execute",
        help="Execute backup (requires live actions explicitly enabled).",
    ),
) -> None:
    """Plan or execute a logical backup (dry-run by default)."""
    from mercury.backup.backup_runner import BackupExecutionError, execute_backup
    from mercury.backup.terminal.runner import print_backup_execution
    from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

    normalized = kind.strip().lower().replace("-", "_")
    if normalized not in (BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY):
        typer.echo("Invalid --kind. Use: full or schema_only")
        raise typer.Exit(1)

    try:
        result = execute_backup(db, normalized, execute=execute)  # type: ignore[arg-type]
    except BackupExecutionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    print_backup_execution(result)
    if result.refused and execute:
        raise typer.Exit(1)


@backup_app.command("batch")
def backup_batch_cmd(
    kind: str = typer.Option("full", "--kind", help="Backup kind: full or schema_only."),
    execute: bool = typer.Option(False, "--execute", help="Execute all backup sources."),
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
    """Plan or execute backups for all approved backup sources."""
    from mercury.backup.batch_runner import (
        BackupSourceSelectionError,
        run_backup_batch,
        select_batch_sources,
    )
    from mercury.backup.terminal.batch import print_backup_batch_result
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
    if batch.errors or (execute and batch.refused_count and not batch.executed_count):
        raise typer.Exit(1)


@backup_app.command("all")
def backup_all_cmd(
    kind: str = typer.Option("full", "--kind", help="Backup kind: full or schema_only."),
    execute: bool = typer.Option(False, "--execute", help="Execute backups for active source databases."),
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
    backup_batch_cmd(kind=kind, execute=execute, db=db, demo=demo)


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
        False,
        "--update-manifest",
        help="Set manifest verified=true when verification passes.",
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
        help="Write database manifest and restore runbook files to the USB target.",
    ),
) -> None:
    """Plan or write one database transfer manifest/runbook set for active source backups."""
    from mercury.backup import (
        build_database_bundle_plan,
        print_database_bundle_plan,
        write_database_bundle_plan,
    )
    from mercury.backup.batch_runner import BackupSourceSelectionError

    try:
        plan = build_database_bundle_plan(live=not demo, selected=db)
    except BackupSourceSelectionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc
    print_database_bundle_plan(plan, executed=False)
    if not execute:
        return
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
        False,
        "--live",
        help="Use live server inventory for production sync pairs.",
    ),
) -> None:
    """Report which production sync pairs have verified full backups."""
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
        help="Restore verified backups into dev targets (requires live actions).",
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
    yes: bool = typer.Option(False, "--yes", help="Skip SYNC DEV confirmation prompt."),
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
    if execute and policy.live_execution_allowed() and not yes:
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
        help="Restore all ready verified backups into dev targets (requires live actions).",
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip SYNC DEV confirmation prompt."),
) -> None:
    """Plan or execute sync for all ready production sync pairs."""
    sync_run_cmd(live=live, execute=execute, source=None, target=None, yes=yes)


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
    if not result.executed:
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
