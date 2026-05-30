"""Database CLI commands (registered on `db` and `database` typer groups)."""

import typer

from mercury import output
from mercury.database import (
    MariaDbConfigError,
    MariaDbDriverMissingError,
    MariaDbLiveError,
    discover,
    discover_demo,
    inspect_database_on_server,
    load_mariadb_config,
    print_classification,
    print_inventory,
    print_prod_dev_pairs,
    probe_mariadb_server,
    validate_config_policy,
)
from mercury.database.mariadb.access import build_platform_access_report
from mercury.database.display_access import print_platform_access
from mercury.database.display_inspect import print_database_inspect
from mercury.database.display_ping import print_server_probe
from mercury.database.display_policy import print_policy_report


def register_commands(app: typer.Typer) -> None:
    """Attach database commands to a Typer app."""

    @app.command("ping")
    def ping_cmd() -> None:
        """Read-only server probe (connect, VERSION, SHOW DATABASES sample)."""
        try:
            print_server_probe(probe_mariadb_server())
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
    def access_cmd() -> None:
        """Compare platform catalog databases to live server (read-only)."""
        try:
            print_platform_access(build_platform_access_report())
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
    ) -> None:
        """Discover databases: --demo offline, else live read-only SHOW DATABASES."""
        if demo:
            print_inventory(discover_demo())
            return
        try:
            print_inventory(discover("live"))
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
        if demo:
            print_inventory(discover_demo())
        else:
            discover_cmd(demo=False)

    @app.command("pairs")
    def pairs_cmd() -> None:
        print_prod_dev_pairs()

    @app.command("classify")
    def classify_cmd(
        name: str = typer.Option(..., "--name", help="Database name to classify."),
    ) -> None:
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
        report = validate_config_policy(use_demo_catalog=demo)
        print_policy_report(report)
        if not report.ok():
            raise typer.Exit(1)

    @app.command("summary")
    def summary_cmd(
        demo: bool = typer.Option(True, "--demo/--no-demo", help="Inventory source."),
    ) -> None:
        """Short summary: counts and backup sources."""
        inv = discover_demo() if demo else discover("live")
        from mercury.database.core import (
            backup_source_names,
            entries_by_role,
            is_live_inventory,
        )

        output.heading("Database module summary")
        output.field("mode", inv.mode)
        output.field("connection", inv.connection)
        output.field("count", inv.count)
        for role, entries in sorted(entries_by_role(inv).items()):
            output.field(f"role_{role}", len(entries))
        output.heading("Backup sources")
        for name in backup_source_names(inv):
            output.item(name)
        output.write()
        if is_live_inventory(inv):
            output.write("Live read-only discovery.")
        else:
            output.write("Offline/demo inventory.")
