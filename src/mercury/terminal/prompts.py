"""Helpers for interactive prompt text.

`input()` and similar readers need plain text, not Rich markup. Keep prompt
formatting here so menu and CLI prompt call sites do not hand markup strings to
raw terminal input functions.
"""

from __future__ import annotations

import re

from mercury.terminal.theme import prompt_text, strip_markup


# Rich interprets bracketed choice hints such as ``[y/N]`` as markup. Preserve
# the small set of literal terminal controls before removing real style tags.
_LITERAL_CONTROL_RE = re.compile(r"\[(?:[yYnN]/[yYnN]|\d+|ok|--|!!|i)\]")


def display_prompt(text: str) -> str:
    """Return the styled prompt text for output-only rendering."""
    return prompt_text(text)


def input_prompt(text: str) -> str:
    """Return plain prompt text safe to pass to input()."""
    return strip_markup(display_prompt(text))


def normalize_input_prompt(prompt: str) -> str:
    """Strip any accidental markup from an already-built prompt string."""
    literals: list[str] = []

    def preserve(match: re.Match[str]) -> str:
        literals.append(match.group(0))
        return f"__MERCURY_LITERAL_CONTROL_{len(literals) - 1}__"

    plain = strip_markup(_LITERAL_CONTROL_RE.sub(preserve, prompt))
    for index, literal in enumerate(literals):
        plain = plain.replace(f"__MERCURY_LITERAL_CONTROL_{index}__", literal)
    return plain


def choice_prompt(*, leading_newline: bool = True) -> str:
    """Canonical ``Choice: `` prompt (colon + trailing space for input echo)."""
    prefix = "\n" if leading_newline else ""
    return input_prompt(f"{prefix}Choice: ")


def ensure_choice_prompt(prompt: str) -> str:
    """Normalize bare Choice labels to the canonical ``Choice: `` form."""
    normalized = normalize_input_prompt(prompt)
    stripped = normalized.strip()
    if stripped in {"Choice", "Choice:"}:
        return choice_prompt(leading_newline=True)
    # Ensure trailing space after a trailing colon so typed input does not glue.
    if stripped.endswith(":") and not normalized.endswith((" ", "\t")):
        return normalized.rstrip() + " "
    return normalized
