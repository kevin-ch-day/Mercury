"""Mercury command-line interface."""

import typer

from mercury.database import build_demo_backup_plan
from mercury.database.cli import register_commands
from mercury.env_probe import format_policy_summary, probe_environment
from mercury.menu import run_menu
from mercury import output
from mercury.safety import DRY_RUN_ONLY, LIVE_ACTIONS_ENABLED

app = typer.Typer(
    name="mercury",
    help="Mercury — database backup, DR, and prod-to-dev sync (seed / dry-run).",
    no_args_is_help=True,
)

env_app = typer.Typer(help="Environment commands.")
db_app = typer.Typer(help="Database commands.")
database_app = typer.Typer(help="Database module (same commands as db).")
backup_app = typer.Typer(help="Backup commands.")
config_app = typer.Typer(help="Configuration commands.")
sync_app = typer.Typer(help="Prod to dev sync (dry-run in seed).")
report_app = typer.Typer(help="Backup report previews (dry-run).")

app.add_typer(env_app, name="env")
app.add_typer(db_app, name="db")
app.add_typer(database_app, name="database")
register_commands(db_app)
register_commands(database_app)
app.add_typer(backup_app, name="backup")
app.add_typer(config_app, name="config")
app.add_typer(sync_app, name="sync")
app.add_typer(report_app, name="report")


@env_app.command("probe")
def env_probe() -> None:
    """Probe the local environment (no database connections)."""
    from mercury.database import build_readonly_discovery_plan, probe_client_tooling

    result = probe_environment()
    tooling = probe_client_tooling()
    readonly_plan = build_readonly_discovery_plan()

    output.heading("Mercury environment probe")
    output.field("python", result.python_version)
    output.field("platform", f"{result.platform_system} ({result.platform_release})")
    output.field("repo_root", result.repo_root)
    output.field("config_dir", result.config_dir)
    output.field("output_dir", result.output_dir)
    output.field("mode", result.mode)
    output.field("dry_run", result.dry_run_only)
    output.field("live_actions", LIVE_ACTIONS_ENABLED)

    output.heading("Operator status")
    from mercury.runtime import operator_status

    for key, value in operator_status().items():
        output.field(key, value)

    output.heading("Config status")
    for key, value in result.config_status.items():
        output.field(key, value)

    output.heading("MariaDB client tools (PATH)")
    for name, path in tooling.tools.items():
        output.field(name, path)

    output.heading("Read-only discovery (next on Fedora)")
    output.field("plan_status", readonly_plan.status)
    for note in readonly_plan.notes[:3]:
        output.bullet(note)

    output.heading("Notes")
    for note in result.notes:
        output.bullet(note)
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
    if not demo:
        if DRY_RUN_ONLY or not LIVE_ACTIONS_ENABLED:
            typer.echo("Seed mode: use --demo for dry-run backup planning.")
            raise typer.Exit(1)
        from mercury.database import build_discovered_backup_plan

        plan = build_discovered_backup_plan()
    else:
        plan = build_demo_backup_plan()

    from mercury.backup_display import print_backup_plan

    print_backup_plan(plan)

    if sample_manifest:
        if not demo:
            typer.echo("--sample-manifest requires --demo in seed mode.")
            raise typer.Exit(1)
        from mercury.sample_manifest import write_sample_manifests

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
    if not demo:
        typer.echo("Seed mode: use --demo for schema-only planning.")
        raise typer.Exit(1)
    from mercury.schema_backup_plan import build_schema_backup_plan_demo
    from mercury.plan_display import print_schema_backup_plan

    print_schema_backup_plan(build_schema_backup_plan_demo())


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
    from mercury.manifest_preview import (
        ManifestPreviewError,
        build_manifest_preview,
        format_manifest_preview_json,
    )
    from mercury.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

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
    from mercury.verification import build_verification_plan_demo
    from mercury.verify_display import print_verification_plan

    print_verification_plan(build_verification_plan_demo())


@backup_app.command("list")
def backup_list_cmd(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="List demo planned backup records from manifest previews.",
    ),
) -> None:
    """Preview backup history (demo planned records only)."""
    if not demo:
        typer.echo("Seed mode: use --demo for backup list preview.")
        raise typer.Exit(1)
    from mercury.backup_list import build_demo_backup_list
    from mercury.verify_display import print_demo_backup_list

    print_demo_backup_list(build_demo_backup_list())


@report_app.command("preview")
def report_preview_cmd(
    db: str = typer.Option(..., "--db", help="Database name."),
    kind: str = typer.Option(..., "--kind", help="full or schema_only"),
) -> None:
    """Markdown-style backup report preview (dry-run; no file write)."""
    from mercury.manifest_preview import ManifestPreviewError
    from mercury.report_preview import build_report_preview, format_report_preview_markdown
    from mercury.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

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
        help="Prod→dev sync plan from platform catalog (required in seed).",
    ),
) -> None:
    """Dry-run prod→dev sync plan with prerequisites (not executed)."""
    if not demo:
        typer.echo("Seed mode: use --demo for sync planning.")
        raise typer.Exit(1)
    from mercury.sync_plan import build_sync_plan_demo
    from mercury.plan_display import print_sync_plan

    print_sync_plan(build_sync_plan_demo())


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
) -> None:
    """Protection snapshot: backup sources, gaps, prod→dev pairs, action items."""
    from mercury.paths import OUTPUT_DIR, PROTECTION_REPORT_FILE
    from mercury.protection_report import build_protection_report, format_protection_report

    report = build_protection_report()
    text = format_protection_report(report)
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
    """Create config/databases.toml and config/local.toml from examples."""
    from mercury.config_init import init_local_config

    output.heading("Initialize local config")
    for line in init_local_config(force=force):
        output.item(line)


@app.command("menu")
def menu_cmd() -> None:
    """Open the Mercury interactive menu."""
    run_menu(interactive=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
