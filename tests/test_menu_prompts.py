"""Tests for shared menu prompts."""

import pytest

from mercury import menu_display
from mercury import menu_prompts
from mercury.menu.subscreen import read_submenu_choice


def test_menu_action_keys_match_sections() -> None:
    expected = [item.key for _section, items in menu_display.MENU_SECTIONS for item in items]
    assert menu_prompts.menu_action_keys() == expected


def test_menu_option_prompt_uses_enter_your_choice() -> None:
    prompt = menu_prompts.menu_option_prompt()
    assert prompt == "\nEnter your choice: "


def test_submenu_option_prompt_uses_choice_with_space() -> None:
    prompt = menu_prompts.submenu_option_prompt()
    assert prompt == "\nChoice: "


def test_ask_strips_markup_before_reader() -> None:
    seen: list[str] = []

    def fake_reader(prompt: str) -> str:
        seen.append(prompt)
        return "3"

    menu_prompts.set_prompt_reader(fake_reader)
    try:
        assert menu_prompts.ask("[bold #00D4FF]Enter your choice:[/bold #00D4FF]") == "3"
    finally:
        menu_prompts.set_prompt_reader(None)

    assert seen == ["Enter your choice:"]


def test_normalize_menu_choice_maps_quit_aliases() -> None:
    assert menu_prompts.normalize_menu_choice("q") == "0"
    assert menu_prompts.normalize_menu_choice("quit") == "0"
    assert menu_prompts.normalize_menu_choice("exit") == "0"
    assert menu_prompts.normalize_menu_choice("  Q  ") == "0"
    assert menu_prompts.normalize_menu_choice("2") == "2"


def test_is_valid_menu_choice() -> None:
    assert menu_prompts.is_valid_menu_choice("0")
    assert menu_prompts.is_valid_menu_choice("q")
    assert menu_prompts.is_valid_menu_choice("6")
    assert menu_prompts.is_valid_menu_choice("8")
    assert not menu_prompts.is_valid_menu_choice("")
    assert not menu_prompts.is_valid_menu_choice("99")


def test_invalid_choice_message_includes_range() -> None:
    message = menu_prompts.invalid_choice_message("99")
    assert menu_prompts.menu_option_range_label() in message
    assert "99" in message


def test_read_menu_option_uses_reader() -> None:
    inputs = iter(["3"])
    menu_prompts.set_prompt_reader(lambda _prompt: next(inputs))
    try:
        assert menu_prompts.read_menu_option() == "3"
    finally:
        menu_prompts.set_prompt_reader(None)


def test_read_menu_option_returns_none_on_eof(capsys: pytest.CaptureFixture[str]) -> None:
    def raise_eof(_prompt: str) -> str:
        raise EOFError

    menu_prompts.set_prompt_reader(raise_eof)
    try:
        assert menu_prompts.read_menu_option() is None
    finally:
        menu_prompts.set_prompt_reader(None)
    assert capsys.readouterr().out == "\n"


def test_wait_for_continue_uses_reader() -> None:
    called = {"count": 0}

    def fake_continue() -> None:
        called["count"] += 1

    menu_prompts.set_continue_reader(fake_continue)
    try:
        menu_prompts.wait_for_continue()
    finally:
        menu_prompts.set_continue_reader(None)
    assert called["count"] == 1


def test_ask_yes_no_accepts_defaults() -> None:
    menu_prompts.set_prompt_reader(lambda _prompt: "")
    try:
        assert menu_prompts.ask_yes_no("Proceed?", default=True) is True
        assert menu_prompts.ask_yes_no("Proceed?", default=False) is False
    finally:
        menu_prompts.set_prompt_reader(None)


def test_ask_yes_no_parses_answer() -> None:
    inputs = iter(["yes", "n"])
    menu_prompts.set_prompt_reader(lambda _prompt: next(inputs))
    try:
        assert menu_prompts.ask_yes_no("Proceed?") is True
        assert menu_prompts.ask_yes_no("Proceed?") is False
    finally:
        menu_prompts.set_prompt_reader(None)


def test_ask_confirmation_phrase_exact_match() -> None:
    seen: list[str] = []

    def yes_reader(prompt: str) -> str:
        seen.append(prompt)
        return "SYNC DEV"

    menu_prompts.set_prompt_reader(yes_reader)
    try:
        assert menu_prompts.ask_confirmation_phrase("SYNC DEV", action="sync") is True
    finally:
        menu_prompts.set_prompt_reader(None)
    assert seen == ["\nConfirmation (sync) [SYNC DEV]: "]

    menu_prompts.set_prompt_reader(lambda _prompt: "sync dev")
    try:
        assert menu_prompts.ask_confirmation_phrase("SYNC DEV", action="sync") is False
    finally:
        menu_prompts.set_prompt_reader(None)

# merged from test_menu_subscreen.py
def test_read_submenu_choice_empty_reprompts(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["", "1"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_stripped",
        lambda _prompt: next(answers),
    )
    assert read_submenu_choice() == "1"

# merged from test_menu_subscreen.py
def test_read_submenu_choice_zero_returns_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mercury.menu.prompts.ask_stripped", lambda _prompt: "0")
    assert read_submenu_choice() == "0"

