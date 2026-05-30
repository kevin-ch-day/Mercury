"""Mercury CLI bootstrap — wire subsystems and session logging without eager imports."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typer

_database_commands_wired = False

_VALUE_OPTIONS = frozenset({"--log-level", "--log-dir"})


def argv_uses_database_commands(argv: Sequence[str] | None = None) -> bool:
    """True when argv invokes the ``db`` or ``database`` Typer group."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUE_OPTIONS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return token in ("db", "database")
    return False


def wire_database_commands(db_app: typer.Typer, database_app: typer.Typer) -> None:
    """Register ``db`` / ``database`` commands (does not load mercury.database)."""
    global _database_commands_wired
    if _database_commands_wired:
        return
    from mercury.db_commands import register_commands

    register_commands(db_app)
    register_commands(database_app)
    _database_commands_wired = True


def prepare_for_argv(
    db_app: typer.Typer,
    database_app: typer.Typer,
    argv: Sequence[str] | None = None,
) -> None:
    """Wire subsystems only when argv needs them."""
    if argv_uses_database_commands(argv):
        wire_database_commands(db_app, database_app)


def init_command_logging(
    *,
    invoked_subcommand: str | None,
    log_level: str | None = None,
    log_dir: str | None = None,
    logging_enabled: bool | None = None,
) -> None:
    """Configure file logging once a subcommand is known."""
    if invoked_subcommand is None:
        return
    from mercury.logging import configure_logging, log_session_start

    configure_logging(
        enabled=logging_enabled,
        level=log_level,
        log_dir=log_dir,
    )
    log_session_start(argv=sys.argv)


def run_with_session_logging(app: typer.Typer) -> None:
    """Run Typer app and record session end / uncaught errors in logs."""
    import atexit

    from mercury.logging import log_session_end, log_uncaught_exception

    state = {"exit_code": 0}

    def _log_exit() -> None:
        log_session_end(exit_code=state["exit_code"])

    atexit.register(_log_exit)
    try:
        app()
    except SystemExit as exc:
        code = exc.code
        state["exit_code"] = int(code) if isinstance(code, int) else 1
        raise
    except Exception:
        log_uncaught_exception()
        state["exit_code"] = 1
        raise
