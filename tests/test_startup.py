"""Tests for lazy CLI startup and bootstrap wiring."""

from __future__ import annotations

import sys


def test_cli_import_does_not_load_database_package() -> None:
    """Importing cli should not eagerly load mercury.database (wired in main())."""
    to_drop = [name for name in sys.modules if name == "mercury.cli" or name.startswith("mercury.database")]
    for name in to_drop:
        del sys.modules[name]

    import mercury.cli  # noqa: F401

    assert "mercury.database" not in sys.modules


def test_wire_database_commands_does_not_load_database_package() -> None:
    """Wiring db commands should not import mercury.database (only db_commands)."""
    to_drop = [
        name
        for name in sys.modules
        if name.startswith("mercury.database") or name in ("mercury.bootstrap", "mercury.db_commands")
    ]
    for name in to_drop:
        del sys.modules[name]

    import mercury.bootstrap as bootstrap
    import typer

    bootstrap._database_commands_wired = False
    bootstrap.wire_database_commands(typer.Typer(), typer.Typer())

    assert "mercury.db_commands" in sys.modules
    assert "mercury.database" not in sys.modules

    bootstrap._database_commands_wired = False


def test_argv_uses_database_commands() -> None:
    from mercury.bootstrap import argv_uses_database_commands

    assert argv_uses_database_commands(["menu"]) is False
    assert argv_uses_database_commands(["backup", "plan"]) is False
    assert argv_uses_database_commands(["--log-level", "DEBUG", "db", "ping"]) is True
    assert argv_uses_database_commands(["database", "discover", "--demo"]) is True
    assert argv_uses_database_commands(["--help"]) is False


def test_prepare_for_argv_skips_wire_for_menu(monkeypatch) -> None:
    import mercury.bootstrap as bootstrap
    import typer

    calls = 0
    original = bootstrap.wire_database_commands

    def counting_wire(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    bootstrap._database_commands_wired = False
    monkeypatch.setattr(bootstrap, "wire_database_commands", counting_wire)

    bootstrap.prepare_for_argv(typer.Typer(), typer.Typer(), ["menu"])
    assert calls == 0
    bootstrap.prepare_for_argv(typer.Typer(), typer.Typer(), ["db", "ping"])
    assert calls == 1

    bootstrap._database_commands_wired = False


def test_wire_database_commands_registers_once(monkeypatch) -> None:
    import mercury.bootstrap as bootstrap
    import mercury.db_commands as db_commands
    import typer

    calls = 0

    def fake_register(_app: typer.Typer) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(db_commands, "register_commands", fake_register)
    bootstrap._database_commands_wired = False

    db = typer.Typer()
    database = typer.Typer()
    bootstrap.wire_database_commands(db, database)
    bootstrap.wire_database_commands(db, database)

    assert calls == 2

    bootstrap._database_commands_wired = False
