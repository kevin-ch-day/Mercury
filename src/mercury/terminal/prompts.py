"""Helpers for interactive prompt text.

`input()` and similar readers need plain text, not Rich markup. Keep prompt
formatting here so menu and CLI prompt call sites do not hand markup strings to
raw terminal input functions.
"""

from __future__ import annotations

from mercury.terminal.theme import prompt_text, strip_markup


def display_prompt(text: str) -> str:
    """Return the styled prompt text for output-only rendering."""
    return prompt_text(text)


def input_prompt(text: str) -> str:
    """Return plain prompt text safe to pass to input()."""
    return strip_markup(display_prompt(text))


def normalize_input_prompt(prompt: str) -> str:
    """Strip any accidental markup from an already-built prompt string."""
    return strip_markup(prompt)

