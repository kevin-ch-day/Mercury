"""Tests for mercury logs CLI commands."""

from __future__ import annotations

import pytest

from tests.conftest import run_cli


@pytest.fixture(scope="module")
def seeded_logs() -> None:
    run_cli("db", "discover", "--demo")


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("logs", "path"), ("log_dir", "error_log", "database_log", "backup_log")),
        (("logs", "list"), ("Mercury logs",)),
        (("logs", "errors"), ("Recent errors",)),
    ],
)
def test_logs_readonly_commands(args: tuple[str, ...], expected: tuple[str, ...]) -> None:
    result = run_cli(*args)
    assert result.returncode == 0, result.stderr
    for snippet in expected:
        assert snippet in result.stdout


def test_logs_status_after_activity(seeded_logs: None) -> None:
    result = run_cli("logs", "status")
    assert result.returncode == 0, result.stderr
    assert "Log status" in result.stdout


def test_logs_search_after_activity(seeded_logs: None) -> None:
    result = run_cli("logs", "search", "inventory")
    assert result.returncode == 0, result.stderr
    assert "Log search" in result.stdout
