"""Tests for menu shell."""

import pytest

from mercury.menu import (
    MENU_SUBTITLE,
    MENU_TITLE,
    handle_menu_choice,
    render_menu_text,
    run_menu,
)


def test_render_menu_text_contains_header_and_items() -> None:
    text = render_menu_text()
    assert MENU_TITLE in text
    assert MENU_SUBTITLE in text
    assert "Mode:" in text
    assert "dry-run" in text
    assert "dry-run only" in text
    assert "[1] Environment Check" in text
    assert "[0] Exit" in text
    assert "Sync Production -> Development" in text


def test_menu_renders_without_crashing(capsys: pytest.CaptureFixture[str]) -> None:
    run_menu(interactive=False)
    captured = capsys.readouterr()
    assert MENU_TITLE in captured.out


def test_handle_exit_choice(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("0") is False
    assert "Exiting Mercury" in capsys.readouterr().out


def test_handle_backup_plan_menu(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("3") is True
    assert "Backup plan (dry-run)" in capsys.readouterr().out


def test_handle_schema_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("4") is True
    out = capsys.readouterr().out
    assert "dry-run only" in out
    assert "SCHEMA-ONLY BACKUP PLAN" in out


def test_handle_sync_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("6") is True
    assert "sync plan" in capsys.readouterr().out.lower()


def test_handle_placeholder_choice(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("7") is True
    assert "Not yet implemented" in capsys.readouterr().out


def test_handle_environment_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("1") is True
    assert "Mode: seed" in capsys.readouterr().out


def test_handle_verify_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("5") is True
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
    assert "BACKUP VERIFICATION PLAN" in out


def test_handle_reports_history(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("8") is True
    out = capsys.readouterr().out
    assert "BACKUP LIST" in out
    assert "Mercury Backup Report" in out


def test_handle_discover_databases(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("2") is True
    out = capsys.readouterr().out
    assert "Known databases" in out
    assert "erebus_threat_intel_prod" in out
    assert "not_connected" in out.lower() or "No database server" in out
