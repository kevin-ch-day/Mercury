"""Tests for mercury logs CLI commands."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import CLI, run_cli, subprocess_env


def test_logs_commands_observe_the_directory_selected_at_startup(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit logs"
    fallback = tmp_path / "fallback"
    env = {"MERCURY_LOG_DIR": str(fallback)}

    def run(*args: str) -> str:
        result = run_cli("--log-dir", str(explicit), *args, env=env)
        assert result.returncode == 0, result.stderr
        return result.stdout

    assert "android_permission_intel" in run("db", "discover", "--demo")
    assert "inventory discovered" in (explicit / "database.log").read_text()
    assert str(explicit) in run("logs", "path")
    assert "database.log" in run("logs", "list")
    assert str(explicit) in run("--no-logging", "logs", "status")
    assert "logging_enabled: False" in run("--no-logging", "logs", "status")
    assert "inventory discovered" in run("logs", "tail", "--database")
    assert "inventory discovered" in run("logs", "search", "inventory", "--in", "database")
    assert "No matches found." in run("logs", "search", "synthetic-private-search-term")
    assert "db discover --demo" in run("--no-logging", "logs", "sessions", "--limit", "50")
    assert not fallback.exists()
    assert all("synthetic-private-search-term" not in path.read_text() for path in explicit.glob("*.log"))


def test_missing_error_log_never_falls_back_to_the_daily_log(tmp_path: Path) -> None:
    daily = tmp_path / "mercury-2026-05-30.log"
    daily.write_text("ordinary historical record\n")
    result = run_cli("--no-logging", "--log-dir", str(tmp_path), "logs", "errors")
    assert result.returncode == 0, result.stderr
    assert "No errors logged." in result.stdout
    assert "ordinary historical record" not in result.stdout
    assert not (tmp_path / "error.log").exists()


def test_default_tail_labels_the_daily_file_it_reads(tmp_path: Path) -> None:
    daily = tmp_path / "mercury-2026-05-30.log"
    daily.write_text("daily record\n")
    os.utime(daily, (100, 100))
    dedicated = tmp_path / "backup.log"
    dedicated.write_text("dedicated record\n")
    os.utime(dedicated, (200, 200))

    result = run_cli("--no-logging", "--log-dir", str(tmp_path), "logs", "tail")
    assert result.returncode == 0, result.stderr
    assert daily.name in result.stdout
    assert "daily record" in result.stdout
    assert dedicated.name not in result.stdout
    assert "dedicated record" not in result.stdout


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
def test_startup_continues_if_a_log_target_is_a_named_pipe(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "error.log")
    result = subprocess.run(
        [*CLI, "--log-dir", str(tmp_path), "logs", "path"],
        env=subprocess_env(), capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Continuing without file logging" in result.stderr
    assert "(logging disabled)" in result.stdout


def test_invalid_top_level_command_does_not_create_an_end_only_session(tmp_path: Path) -> None:
    result = run_cli("--log-dir", str(tmp_path), "unknown-mercury-command")
    assert result.returncode != 0
    assert not list(tmp_path.glob("mercury-*.log"))
