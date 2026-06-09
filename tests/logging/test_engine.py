"""Tests for logging engine, config, and utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from mercury.logging import (
    backup_log_path,
    configure_logging,
    current_backup_log_file,
    current_database_log_file,
    current_error_log_file,
    current_log_file,
    daily_log_path,
    database_log_path,
    error_log_path,
    get_logger,
    list_log_files,
    log_operation,
    log_session_end,
    log_session_start,
    read_log_tail,
    reset_logging,
    search_log_files,
)

from tests.logging.helpers import flush_logs, read_log, read_main_log


def test_configure_logging_daily_file(log_dir: Path) -> None:
    path = configure_logging(enabled=True, log_dir=log_dir, level="INFO")
    assert path == daily_log_path(log_dir=log_dir)
    assert path.parent == log_dir

    get_logger().info("hello mercury")
    text = read_main_log()
    assert "hello mercury" in text
    assert "[" in text and "]" in text


def test_logging_disabled(log_dir: Path) -> None:
    assert configure_logging(enabled=False, log_dir=log_dir) is None
    assert current_log_file() is None


def test_log_session_lifecycle(log_dir: Path) -> None:
    configure_logging(enabled=True, log_dir=log_dir)
    log_session_start(argv=["mercury", "db", "discover"])
    log_session_end(exit_code=0)
    log_session_end(exit_code=1)

    main_text = read_main_log()
    assert "session start" in main_text
    assert "mercury db discover" in main_text
    assert "exit_code=0" in main_text
    assert "exit_code=1" in main_text

    error_text = read_log(current_error_log_file)
    assert "exit_code=1" in error_text
    assert "exit_code=0" not in error_text


def test_list_and_tail_log_files(log_dir: Path) -> None:
    configure_logging(enabled=True, log_dir=log_dir)
    logger = get_logger()
    for index in range(3):
        logger.info("line-%s", index)
    flush_logs()

    files = list_log_files(log_dir=log_dir)
    assert len(files) == 1
    tail = read_log_tail(lines=2, log_file=files[0])
    assert len(tail) == 2
    assert "line-2" in tail[-1]


def test_log_operation_success_and_failure(log_dir: Path) -> None:
    configure_logging(enabled=True, log_dir=log_dir, level="DEBUG")

    with log_operation("test-op", item="a"):
        pass
    main_text = read_main_log()
    assert "operation start" in main_text
    assert "operation ok" in main_text
    assert "elapsed_ms=" in main_text

    with pytest.raises(RuntimeError, match="boom"):
        with log_operation("fail-op"):
            raise RuntimeError("boom")

    main_text = read_main_log()
    error_text = read_log(current_error_log_file)
    assert "operation failed" in main_text
    assert "boom" in main_text
    assert "operation failed" in error_text


def test_search_log_files(log_dir: Path) -> None:
    configure_logging(enabled=True, log_dir=log_dir)
    get_logger("mercury.backup").info("backup start database=%s", "prod_db")
    flush_logs()

    matches = search_log_files("prod_db", log_dir=log_dir)
    assert matches
    assert any("prod_db" in line for _, _, line in matches)


@pytest.mark.parametrize(
    ("logger_name", "level", "message", "path_fn", "current_fn", "snippet"),
    [
        (
            "mercury",
            "error",
            "something broke",
            error_log_path,
            current_error_log_file,
            "something broke",
        ),
        (
            "mercury.database",
            "info",
            "inventory discovered mode='live' count=7",
            database_log_path,
            current_database_log_file,
            "inventory discovered",
        ),
        (
            "mercury.backup",
            "info",
            "batch backup executed=1",
            backup_log_path,
            current_backup_log_file,
            "batch backup",
        ),
        (
            "mercury.menu",
            "info",
            "menu event",
            daily_log_path,
            current_log_file,
            "menu event",
        ),
    ],
)
def test_log_routing(
    log_dir: Path,
    logger_name: str,
    level: str,
    message: str,
    path_fn,
    current_fn,
    snippet: str,
) -> None:
    configure_logging(enabled=True, log_dir=log_dir)
    getattr(get_logger(logger_name), level)(message)
    flush_logs()

    assert current_fn() == path_fn(log_dir=log_dir)
    assert snippet in read_log(current_fn)

# merged from test_logging_permissions.py
def test_configure_logging_survives_permission_error(monkeypatch, tmp_path: Path, capsys) -> None:
    reset_logging()
    log_dir = tmp_path / "mercury_logs"
    log_dir.mkdir()
    log_dir.chmod(0o555)

    def fail_mkdir(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("mercury.logging.engine.resolve_log_dir", lambda **kwargs: log_dir)
    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    result = configure_logging(enabled=True)
    captured = capsys.readouterr()
    assert result is None
    assert "chown" in captured.err.lower() or "doctor" in captured.err.lower() or "repair" in captured.err.lower()
    reset_logging()

    log_dir.chmod(0o755)

