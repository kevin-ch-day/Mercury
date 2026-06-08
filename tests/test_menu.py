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


def test_render_menu_text_contains_header_and_items() -> None:
    text = render_menu_text()
    assert MENU_TITLE in text
    assert MENU_SUBTITLE in text
    assert "Environment" in text
    assert "Execution Safety" in text
    assert "Backup Storage" in text
    assert "Protection" in text
    assert "Mode" in text
    assert "Blocker" in text
    assert "      [1] Environment check" in text
    assert "      [0] Exit" in text
    assert "Sync readiness" in text


def test_render_menu_text_shows_database_status_when_configured() -> None:
    from mercury.core.runtime import should_probe_database_status

    text = render_menu_text()
    if should_probe_database_status():
        assert "MariaDB" in text and "[ok]" in text
    else:
        assert "MariaDB" in text and "[!!]" in text


def test_run_menu_redisplay_after_choice(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    inputs = iter(["1", "0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda _prompt="": next(inputs),
    )
    monkeypatch.setattr("mercury.env.interactive_menu.read_env_choice", lambda: "0")

    run_menu(interactive=True)
    out = capsys.readouterr().out
    assert out.count("MERCURY OPERATOR CONSOLE") == 1
    assert out.count("[1] Environment check") >= 2
    assert "Rescan" in out
    assert "Live mode guide" in out
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
    assert interactive_handle_choice("99") == "invalid"
    out = capsys.readouterr().out
    assert "Invalid choice" in out
    assert "0-8" in out


def test_handle_sync_plan_returns_to_menu_without_footer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("mercury.sync.interactive_menu.read_sync_choice", lambda: "0")
    assert handle_menu_choice("6") == "continue"
    out = capsys.readouterr().out
    assert "ready" in out.lower() or "blocked" in out.lower()
    assert "Rescan readiness" in out
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
        ("1", ("Environment check", "python:", "dry_run:", "Rescan", "Live mode guide")),
        ("2", ("databases:", "roles:", "DATABASE", "ENV", "Rescan inventory")),
        ("3", ("Rescan plan", "sources:", "DATABASE")),
        ("4", ("sources:", "Rescan plan")),
        ("5", ("verified", "Rescan")),
        ("6", ("ready", "blocked", "Rescan readiness")),
        ("7", ("ready", "blocked", "Rescan plans", "Run allowed")),
        ("8", ("backup_root", "inventory")),
    ],
)
def test_handle_menu_action(
    choice: str,
    snippets: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if choice == "1":
        monkeypatch.setattr("mercury.env.interactive_menu.read_env_choice", lambda: "0")
    if choice == "2":
        monkeypatch.setattr("mercury.database.discovery_menu.read_discover_choice", lambda: "0")
    if choice == "6":
        monkeypatch.setattr("mercury.sync.interactive_menu.read_sync_choice", lambda: "0")
    if choice == "3":
        monkeypatch.setattr("mercury.backup.interactive_menu.read_backup_choice", lambda: "0")
    if choice == "5":
        monkeypatch.setattr("mercury.verify.interactive_menu.read_verify_choice", lambda: "0")
    if choice == "4":
        monkeypatch.setattr("mercury.schema.interactive_menu.read_schema_choice", lambda: "0")
    if choice == "7":
        monkeypatch.setattr("mercury.restore.interactive_menu.read_restore_choice", lambda: "0")
    assert handle_menu_choice(choice) == "continue"
    out = capsys.readouterr().out.lower()
    matched = sum(1 for snippet in snippets if snippet.lower() in out)
    assert matched >= 1, f"expected one of {snippets!r} in menu output"
