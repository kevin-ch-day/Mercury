"""Tests for menu shell and interactive loop."""

from __future__ import annotations

import pytest

from mercury import menu_display
from mercury.menu.runners import (
    MENU_SUBTITLE,
    MENU_TITLE,
    handle_menu_choice,
    render_menu_text,
    run_menu,
)
from mercury.menu.loop import handle_menu_choice as interactive_handle_choice


def test_render_menu_text_shows_database_status_when_configured() -> None:
    from mercury.core.runtime import should_probe_database_status

    text = render_menu_text()
    if should_probe_database_status():
        assert "MariaDB" in text and "[ok]" in text
    else:
        assert "MariaDB" in text and "[!!]" in text


def test_run_menu_redisplay_after_choice(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    inputs = iter(["4", "0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda _prompt="": next(inputs),
    )
    monkeypatch.setattr("mercury.env.interactive_menu.read_env_choice", lambda: "0")

    run_menu(interactive=True)
    out = capsys.readouterr().out
    assert out.count("MERCURY OPERATOR CONSOLE") == 1
    assert out.count("[4] Environment details") >= 2
    assert "Rescan" in out
    assert "LIVE MODE GUIDE" not in out
    assert "Press any key to continue" not in out
    assert "[0] Return" not in out
    assert "Exiting Mercury" in out


def test_run_menu_invalid_choice_does_not_redisplay(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    inputs = iter(["9", "0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda _prompt="": next(inputs),
    )

    run_menu(interactive=True)
    out = capsys.readouterr().out
    assert out.count("MERCURY OPERATOR CONSOLE") == 1
    assert "Invalid choice" in out


def test_run_menu_loop_with_injected_renderer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    renders = iter(["MENU-A", "MENU-B"])
    inputs = iter(["0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda _prompt="": next(inputs),
    )

    run_menu(interactive=True, render_menu_text=lambda: next(renders))
    out = capsys.readouterr().out
    assert "MENU-A" in out
    assert "Exiting Mercury" in out


def test_handle_exit_choice(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("0") == "exit"
    assert "Exiting Mercury" in capsys.readouterr().out


def test_handle_quit_alias() -> None:
    assert handle_menu_choice("q") == "exit"


def test_handle_empty_choice() -> None:
    assert handle_menu_choice("") == "empty"
    assert handle_menu_choice("   ") == "empty"


def test_handle_invalid_choice_includes_range(capsys: pytest.CaptureFixture[str]) -> None:
    from mercury.menu.prompts import menu_option_range_label

    assert interactive_handle_choice("99") == "invalid"
    out = capsys.readouterr().out
    assert "Invalid choice" in out
    assert menu_option_range_label() in out


def test_handle_sync_plan_returns_to_menu_without_footer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("mercury.sync.interactive_menu.read_sync_choice", lambda: "0")
    assert handle_menu_choice("2") == "continue"
    out = capsys.readouterr().out
    assert "ready" in out.lower() or "blocked" in out.lower()
    assert "Recheck Database Sync Status" in out
    assert "[0] Return" not in out
    assert "CLI:" not in out


def test_handle_help_choice(capsys: pytest.CaptureFixture[str]) -> None:
    assert handle_menu_choice("?") == "empty"
    out = capsys.readouterr().out
    assert "Operator console help" in out


def test_menu_renders_without_crashing(capsys: pytest.CaptureFixture[str]) -> None:
    run_menu(interactive=False)
    captured = capsys.readouterr()
    assert MENU_TITLE in captured.out


@pytest.mark.parametrize(
    ("choice", "snippets"),
    [
        ("1", ("USB Path", "LAST BACKUP", "Run full backup now", "Restore-check source backups")),
        ("2", ("ready", "blocked", "Recheck Database Sync Status")),
        ("3", ("REPORTS AND BACKUP HISTORY", "Backup root", "Show backup history", "Show protection status")),
        ("4", ("ENVIRONMENT CHECK", "Runtime", "Live mode guide")),
        ("5", ("Active scope:", "Backup sources:", "DATABASE", "ROLE", "Rescan inventory")),
            ("6", ("OPERATOR SAFETY GUIDE", "Destructive actions", "Backups write to USB")),
        ("7", ("MERCURY DOCTOR", "Repo", "Recommended Next Step")),
        ("8", ("Deploy to This System", "Deploy databases", "Deploy repositories")),
    ],
)
def test_handle_menu_action(
    choice: str,
    snippets: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if choice == "4":
        monkeypatch.setattr("mercury.env.interactive_menu.read_env_choice", lambda: "0")
    if choice == "5":
        monkeypatch.setattr("mercury.database.discovery_menu.read_discover_choice", lambda: "0")
    if choice == "2":
        monkeypatch.setattr("mercury.sync.interactive_menu.read_sync_choice", lambda: "0")
    if choice == "1":
        monkeypatch.setattr("mercury.backup.interactive_menu.read_backup_choice", lambda: "0")
    if choice == "3":
        monkeypatch.setattr("mercury.reporting.interactive_menu.read_reports_choice", lambda: "0")
    if choice == "6":
        monkeypatch.setattr("mercury.menu.prompts.wait_for_continue", lambda *args, **kwargs: None)
    if choice == "7":
        monkeypatch.setattr("mercury.env.interactive_menu.read_submenu_choice", lambda: "0")
    if choice == "8":
        monkeypatch.setattr("mercury.deploy.interactive_menu.read_deploy_choice", lambda: "0")
    assert handle_menu_choice(choice) == "continue"
    out = capsys.readouterr().out.lower()
    matched = sum(1 for snippet in snippets if snippet.lower() in out)
    assert matched >= 1, f"expected one of {snippets!r} in menu output"

# merged from test_menu_display.py
def test_status_line_includes_tags() -> None:
    line = menu_display.status_line(probe_database=False)
    assert "Status:" in line
    assert "[--]" in line or "[ok]" in line

# merged from test_menu_display.py
def test_status_rows_include_operator_fields() -> None:
    rows = menu_display.status_rows(probe_database=False)
    text = "\n".join(rows)
    assert "Mode" in text
    assert "Database" in text
    assert "Backups" in text

# merged from test_menu_display.py
def test_format_menu_bottom_option_exit_and_return() -> None:
    assert menu_display.format_menu_bottom_option("Exit") == "      [0] Exit"
    assert menu_display.format_menu_bottom_option("Return") == "      [0] Return"

# merged from test_menu_display.py
def test_render_option_menu_puts_return_last() -> None:
    text = menu_display.render_option_menu(
        title="Sub menu",
        options=[("1", "First"), ("2", "Second")],
        bottom_label="Return",
    )
    lines = text.splitlines()
    assert lines[-1] == "      [0] Return"
    assert "      [1] First" in lines

# merged from test_menu_display.py
def test_render_main_menu_matches_simple_layout() -> None:
    text = menu_display.render_main_menu()
    assert menu_display.MENU_TITLE in text
    assert menu_display.MENU_SUBTITLE in text
    assert "\nMain Menu\n" in text
    assert "Backup target" in text
    assert "USB backups" in text
    assert "Execution Safety" not in text
    assert "─" in text
    assert "      [1] Backup source databases" in text
    assert "      [0] Exit" in text
    assert "Core workflows" not in text
    assert "Diagnostics" not in text
    assert f"{menu_display.MENU_SUBTITLE}\n────────────────" not in text

# merged from test_menu_display.py
def test_render_main_menu_body_omits_title_block() -> None:
    body = menu_display.render_main_menu_body()
    assert menu_display.MENU_TITLE not in body
    assert "Main Menu" in body
    assert "MariaDB" in body
    assert "      [1] Backup source databases" in body

# merged from test_menu_display.py
def test_render_menu_help_lists_shortcuts() -> None:
    help_text = menu_display.render_menu_help()
    assert "Operator console help" in help_text
    assert "0 or q to exit" in help_text

