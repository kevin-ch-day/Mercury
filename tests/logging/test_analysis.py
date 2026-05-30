"""Tests for log analysis helpers."""

from __future__ import annotations

from pathlib import Path

from mercury.logging import configure_logging, get_logger, log_session_end, log_session_start
from mercury.logging.analysis import analyze_log_file, build_log_status, parse_recent_sessions, resolve_named_log_file

from tests.logging.helpers import flush_logs


def test_analyze_log_file_counts_levels(tmp_path: Path) -> None:
    path = tmp_path / "sample.log"
    path.write_text(
        "2026-05-30 10:00:00 [abc] INFO mercury ok\n"
        "2026-05-30 10:00:01 [abc] ERROR mercury fail\n"
        "2026-05-30 10:00:02 [abc] WARNING mercury warn\n",
        encoding="utf-8",
    )
    info = analyze_log_file(path)
    assert info.lines == 3
    assert info.errors == 1
    assert info.warnings == 1


def test_parse_recent_sessions(log_dir: Path) -> None:
    configure_logging(enabled=True, log_dir=log_dir, level="INFO")
    log_session_start(argv=["mercury", "db", "discover"])
    log_session_end(exit_code=0)
    flush_logs()

    sessions = parse_recent_sessions(log_dir=log_dir)
    assert len(sessions) == 1
    assert sessions[0].exit_code == 0
    assert sessions[0].command == "mercury db discover"


def test_build_log_status_includes_all_files(log_dir: Path) -> None:
    configure_logging(enabled=True, log_dir=log_dir)
    get_logger("mercury.database").info("db event")
    get_logger("mercury.backup").info("backup event")
    get_logger().error("root error")
    flush_logs()

    report = build_log_status(log_dir=log_dir)
    names = {item.name for item in report.files}
    assert {"database.log", "backup.log", "error.log"}.issubset(names)
    assert report.total_errors >= 1
    assert report.logging_enabled is True


def test_resolve_named_log_file(log_dir: Path) -> None:
    configure_logging(enabled=True, log_dir=log_dir)
    get_logger().info("main line")
    flush_logs()

    assert resolve_named_log_file("errors", log_dir=log_dir).name == "error.log"
    assert resolve_named_log_file("database", log_dir=log_dir).name == "database.log"
    assert resolve_named_log_file("backup", log_dir=log_dir).name == "backup.log"
    main = resolve_named_log_file("main", log_dir=log_dir)
    assert main is not None and main.name.startswith("mercury-")
