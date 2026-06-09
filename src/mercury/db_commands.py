"""Database CLI commands — lives outside ``mercury.database`` so wiring stays lightweight."""

from __future__ import annotations

import typer

from mercury import output


def register_commands(app: typer.Typer) -> None:
    """Attach database commands to a Typer app."""

    @app.command("ping")
    def ping_cmd(
        verbose: bool = typer.Option(
            False,
            "--verbose",
            help="Show full probe details (SQL, sample databases, notes).",
        ),
    ) -> None:
        """Read-only server probe (connect, VERSION, SHOW DATABASES sample)."""
        from mercury.database import (
            MariaDbConfigError,
            MariaDbDriverMissingError,
            MariaDbLiveError,
            probe_mariadb_server,
        )
        from mercury.database.terminal.ping import print_server_probe

        try:
            print_server_probe(probe_mariadb_server(), compact=not verbose)
        except MariaDbConfigError as exc:
            typer.echo(str(exc))
            typer.echo("Run: mercury config init")
            typer.echo(
                "For local Fedora socket auth, set use_client=true and unix_socket in local.toml."
            )
            raise typer.Exit(1) from exc
        except MariaDbDriverMissingError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        except MariaDbLiveError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc

    @app.command("inspect")
    def inspect_cmd(
        name: str = typer.Option(..., "--name", help="Database name to inspect on server."),
    ) -> None:
        """Read-only inspect one database (existence, tables, size)."""
        from mercury.database import (
            MariaDbConfigError,
            MariaDbLiveError,
            inspect_database_on_server,
            load_mariadb_config,
        )
        from mercury.database.terminal.inspect import print_database_inspect

        try:
            config = load_mariadb_config()
            print_database_inspect(inspect_database_on_server(name, config))
        except MariaDbConfigError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        except MariaDbLiveError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc

    @app.command("access")
    def access_cmd(
        verbose: bool = typer.Option(
            False,
            "--verbose",
            help="Include connection details, all fields, and footer notes.",
        ),
    ) -> None:
        """Compare platform catalog databases to live server (read-only)."""
        from mercury.database import MariaDbConfigError, MariaDbLiveError
        from mercury.database.mariadb.access import build_platform_access_report
        from mercury.database.terminal.access import print_platform_access

        try:
            print_platform_access(build_platform_access_report(), compact=not verbose)
        except MariaDbConfigError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        except MariaDbLiveError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc

    @app.command("active")
    def active_cmd(
        compact: bool = typer.Option(
            True,
            "--compact/--verbose",
            help="Compact operator table or include footer notes.",
        ),
    ) -> None:
        """Read-only snapshot of the active Mercury database scope."""
        from mercury.database import MariaDbConfigError, MariaDbLiveError, fetch_active_scope_report, load_mariadb_config
        from mercury.database.terminal.active_scope import print_active_scope_report

        try:
            config = load_mariadb_config()
            print_active_scope_report(fetch_active_scope_report(config), compact=compact)
        except MariaDbConfigError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        except MariaDbLiveError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc

    @app.command("discover")
    def discover_cmd(
        demo: bool = typer.Option(
            False,
            "--demo",
            help="Platform catalog + config (no server).",
        ),
        verbose: bool = typer.Option(
            False,
            "--verbose",
            help="Include mode, config source, and footer notes.",
        ),
    ) -> None:
        """Discover databases: --demo offline, else live read-only SHOW DATABASES."""
        from mercury.database import (
            MariaDbConfigError,
            MariaDbDriverMissingError,
            MariaDbLiveError,
            discover,
            discover_demo,
            print_inventory,
        )

        if demo:
            print_inventory(discover_demo(), compact=not verbose)
            return
        try:
            print_inventory(discover("live"), compact=not verbose)
        except MariaDbConfigError as exc:
            typer.echo(str(exc))
            typer.echo("Use --demo for catalog discovery without a database server.")
            raise typer.Exit(1) from exc
        except MariaDbDriverMissingError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        except MariaDbLiveError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc

    @app.command("list")
    def list_cmd(
        demo: bool = typer.Option(True, "--demo/--no-demo", help="Same as discover --demo."),
    ) -> None:
        from mercury.database import discover_demo, print_inventory

        if demo:
            print_inventory(discover_demo())
        else:
            discover_cmd(demo=False)

    @app.command("pairs")
    def pairs_cmd() -> None:
        from mercury.database import print_prod_dev_pairs

        print_prod_dev_pairs()

    @app.command("classify")
    def classify_cmd(
        name: str = typer.Option(..., "--name", help="Database name to classify."),
    ) -> None:
        from mercury.database import print_classification

        print_classification(name)

    @app.command("validate")
    def validate_cmd(
        demo: bool = typer.Option(
            False,
            "--demo",
            help="Validate demo/catalog inventory instead of config files only.",
        ),
    ) -> None:
        """Validate database names against Mercury backup policy."""
        from mercury.database import validate_config_policy
        from mercury.database.terminal.policy import print_policy_report

        report = validate_config_policy(use_demo_catalog=demo)
        print_policy_report(report)
        if not report.ok():
            raise typer.Exit(1)

    @app.command("sizes")
    def sizes_cmd() -> None:
        """Read-only batch sizes for all user databases (one query)."""
        from mercury.database import MariaDbConfigError, MariaDbLiveError, load_mariadb_config
        from mercury.database.mariadb.stats import fetch_all_database_stats
        from mercury.database.terminal.stats import print_database_stats

        try:
            config = load_mariadb_config()
            print_database_stats(fetch_all_database_stats(config))
        except MariaDbConfigError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        except MariaDbLiveError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc

    @app.command("summary")
    def summary_cmd(
        demo: bool = typer.Option(True, "--demo/--no-demo", help="Inventory source."),
        sizes: bool = typer.Option(
            False,
            "--sizes/--no-sizes",
            help="Include live batch size stats (requires --no-demo).",
        ),
    ) -> None:
        """Short summary: counts and backup sources."""
        from mercury.database import (
            MariaDbConfigError,
            MariaDbLiveError,
            discover,
            discover_demo,
            load_mariadb_config,
        )
        from mercury.database.core import (
            backup_source_names,
            entries_by_role,
            is_live_inventory,
        )
        from mercury.database.mariadb.stats import fetch_all_database_stats

        inv = discover_demo() if demo else discover("live")
        output.heading("Database module summary")
        output.field("mode", inv.mode)
        output.field("connection", inv.connection)
        output.field("count", inv.count)
        for role, entries in sorted(entries_by_role(inv).items()):
            output.field(f"role_{role}", len(entries))
        output.heading("Backup sources")
        for name in backup_source_names(inv):
            output.item(name)
        if sizes and not demo:
            try:
                config = load_mariadb_config()
                stats = fetch_all_database_stats(config)
                output.heading("Live sizes")
                output.field("total_bytes", stats.total_bytes)
                for entry in stats.databases:
                    output.item(
                        f"{entry.name}: {_format_summary_bytes(entry.total_bytes)} "
                        f"({entry.table_count} tables)"
                    )
            except MariaDbLiveError as exc:
                output.field("sizes_error", str(exc))
        output.write()
        if is_live_inventory(inv):
            output.write("Live read-only discovery.")
        else:
            output.write("Offline/demo inventory.")


def _format_summary_bytes(value: int) -> str:
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.2f} MiB"
