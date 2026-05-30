"""Tests for shared submenu helpers."""

from __future__ import annotations

import pytest

from mercury.menu.subscreen import read_submenu_choice


def test_read_submenu_choice_empty_reprompts(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["", "1"])
    monkeypatch.setattr(
        "mercury.menu.prompts.ask_stripped",
        lambda _prompt: next(answers),
    )
    assert read_submenu_choice() == "1"


def test_read_submenu_choice_zero_returns_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mercury.menu.prompts.ask_stripped", lambda _prompt: "0")
    assert read_submenu_choice() == "0"
