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
    assert "Status" in text
    assert "Execution mode" in text
    assert "Backup target" in text
    assert "Blocker" in text
    assert "      [1] Backup source databases" in text
    assert "      [0] Exit" in text
    assert "Core workflows" not in text
    assert "Diagnostics" not in text


def test_render_menu_text_shows_database_status_when_configured() -> None:
    from mercury.core.runtime import should_probe_database_status

    text = render_menu_text()
    if should_probe_database_status():
        assert "MariaDB" in text and "[ok]" in text
    else:
        assert "MariaDB" in text and "[!!]" in text


def test_run_menu_redisplay_after_choice(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    inputs = iter(["6", "0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda _prompt="": next(inputs),
    )
    monkeypatch.setattr("mercury.env.interactive_menu.read_env_choice", lambda: "0")

    run_menu(interactive=True)
    out = capsys.readouterr().out
    assert out.count("MERCURY OPERATOR CONSOLE") == 1
    assert out.count("[6] Environment details") >= 2
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
    assert interactive_handle_choice("99") == "invalid"
    out = capsys.readouterr().out
    assert "Invalid choice" in out
    assert "0-8" in out


def test_handle_sync_plan_returns_to_menu_without_footer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("mercury.sync.interactive_menu.read_sync_choice", lambda: "0")
    assert handle_menu_choice("4") == "continue"
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
        ("1", ("Backup root:", "Source databases:", "Production sources", "Run full backup")),
        ("2", ("verified", "Rescan")),
        ("3", ("ready", "blocked", "Rescan plans", "Run allowed", "restore-check backup")),
        ("4", ("ready", "blocked", "Rescan readiness")),
        ("5", ("Backup root", "Active scope", "Source databases")),
        ("6", ("ENVIRONMENT CHECK", "Runtime", "Live mode guide")),
        ("7", ("Active scope:", "Backup sources:", "DATABASE", "ROLE", "Rescan inventory")),
        ("8", ("LIVE MODE GUIDE", "Before enabling live writes", "How to enable live writes")),
    ],
)
def test_handle_menu_action(
    choice: str,
    snippets: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if choice == "6":
        monkeypatch.setattr("mercury.env.interactive_menu.read_env_choice", lambda: "0")
    if choice == "7":
        monkeypatch.setattr("mercury.database.discovery_menu.read_discover_choice", lambda: "0")
    if choice == "4":
        monkeypatch.setattr("mercury.sync.interactive_menu.read_sync_choice", lambda: "0")
    if choice == "1":
        monkeypatch.setattr("mercury.backup.interactive_menu.read_backup_choice", lambda: "0")
    if choice == "2":
        monkeypatch.setattr("mercury.verify.interactive_menu.read_verify_choice", lambda: "0")
    if choice == "3":
        monkeypatch.setattr("mercury.restore.interactive_menu.read_restore_choice", lambda: "0")
    if choice in {"5", "8"}:
        monkeypatch.setattr("mercury.menu.prompts.wait_for_continue", lambda *args, **kwargs: None)
    assert handle_menu_choice(choice) == "continue"
    out = capsys.readouterr().out.lower()
    matched = sum(1 for snippet in snippets if snippet.lower() in out)
    assert matched >= 1, f"expected one of {snippets!r} in menu output"
