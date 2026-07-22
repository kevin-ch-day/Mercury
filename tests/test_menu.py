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
from mercury.menu.actions import MenuAction
from mercury.menu.loop import handle_menu_choice as interactive_handle_choice


def test_render_menu_text_shows_database_status_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.menu.main_display.dashboard_rows",
        lambda **_kwargs: ["  Active writer", "  Database backups", "  Sync readiness"],
    )
    text = render_menu_text()
    assert "Active writer" in text
    assert "Database backups" in text
    assert "Sync readiness" in text


def test_menu_handoff_shortcut_runs_handoff_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"handoff": False}

    def _fake_handoff(**kwargs) -> None:
        called["handoff"] = True

    monkeypatch.setattr("mercury.handoff.interactive_menu.run_handoff_menu", _fake_handoff)
    assert interactive_handle_choice("h") == "continue"
    assert called["handoff"] is True


def test_run_menu_redisplay_after_choice(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    inputs = iter(["4", "0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda _prompt="": next(inputs),
    )
    monkeypatch.setattr("mercury.repair.startup.maybe_prompt_usb_repair_at_startup", lambda: None)
    calls: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.loop.resolve_menu_action",
        lambda choice: MenuAction(choice, "Environment details", lambda: calls.append(choice)),
    )

    run_menu(interactive=True, render_menu_text=lambda: "MERCURY OPERATOR CONSOLE\n[4] Environment details")
    out = capsys.readouterr().out
    assert calls == ["4"]
    assert out.count("MERCURY OPERATOR CONSOLE") == 1
    assert "Exiting Mercury" in out


def test_run_menu_terminates_after_scripted_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A finite scripted input sequence must not cause a hidden redisplay loop."""
    inputs = iter(["1", "0"])
    calls: list[str] = []
    monkeypatch.setattr("mercury.menu.prompts.ask", lambda _prompt="": next(inputs))
    monkeypatch.setattr("mercury.repair.startup.maybe_prompt_usb_repair_at_startup", lambda: None)
    monkeypatch.setattr(
        "mercury.menu.loop.resolve_menu_action",
        lambda choice: MenuAction(choice, "Stub", lambda: calls.append(choice)),
    )

    run_menu(interactive=True, render_menu_text=lambda: "MENU")

    assert calls == ["1"]


def test_run_menu_invalid_choice_does_not_redisplay(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    inputs = iter(["99", "0"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask",
        lambda _prompt="": next(inputs),
    )
    monkeypatch.setattr("mercury.repair.startup.maybe_prompt_usb_repair_at_startup", lambda: None)

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
    monkeypatch.setattr("mercury.repair.startup.maybe_prompt_usb_repair_at_startup", lambda: None)

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
    ("choice",),
    [
        ("1",), ("2",), ("3",), ("4",), ("5",), ("6",), ("7",), ("8",),
    ],
)
def test_handle_menu_action(
    choice: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "mercury.menu.loop.resolve_menu_action",
        lambda key: MenuAction(key, f"Action {key}", lambda: calls.append(key)),
    )
    assert handle_menu_choice(choice) == "continue"
    assert calls == [choice]

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
def test_render_main_menu_matches_simple_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.menu.main_display.dashboard_rows",
        lambda **_kwargs: ["  Active writer", "  Database backups", "  Sync readiness", "  Cutover blockers"],
    )
    text = menu_display.render_main_menu()
    assert menu_display.MENU_TITLE in text
    assert menu_display.MENU_SUBTITLE in text
    assert "\nMain Menu\n" in text
    assert "Active writer" in text
    assert "Database backups" in text
    assert "Sync readiness" in text
    assert "Cutover blockers" in text
    assert "Execution Safety" not in text
    assert "─" in text
    assert "      [1] Backup source databases" in text
    assert "      [4] Sync Offline GitHub Repositories" in text
    assert "      [8] System Deployment" in text
    assert "      [9] Disaster Recovery" in text
    assert "      [10] Workstation handoff" in text
    assert "      [0] Exit" in text
    assert "Operator-storage checklist" not in text
    assert "Core workflows" not in text
    assert "Diagnostics" not in text
    assert f"{menu_display.MENU_SUBTITLE}\n────────────────" not in text


def test_main_menu_options_are_single_action_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mercury.menu.main_display.dashboard_rows", lambda **_kwargs: [])
    text = menu_display.render_main_menu(probe_database=False)
    option_lines = [line for line in text.splitlines() if line.lstrip().startswith("[")]
    expected = [item.key for _section, items in menu_display.MENU_SECTIONS for item in items] + ["0"]
    assert [line.strip().split("]", 1)[0][1:] for line in option_lines] == expected
    assert all(" — " not in line and "→" not in line for line in option_lines)

# merged from test_menu_display.py
def test_render_main_menu_body_omits_title_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mercury.menu.main_display.dashboard_rows",
        lambda **_kwargs: ["  Active writer"],
    )
    body = menu_display.render_main_menu_body()
    assert menu_display.MENU_TITLE not in body
    assert "Main Menu" in body
    assert "Active writer" in body
    assert "      [1] Backup source databases" in body

# merged from test_menu_display.py
def test_render_menu_help_lists_shortcuts() -> None:
    help_text = menu_display.render_menu_help()
    assert "Operator console help" in help_text
    assert "0 or q to exit" in help_text
    assert "transfer receive" in help_text
    assert "handoff (menu 10)" in help_text
