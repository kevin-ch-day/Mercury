"""Tests for shared CLI output helpers."""

from mercury.core import output
from mercury.terminal.theme import set_color_enabled, strip_markup


def test_action_banner(capsys) -> None:
    output.action_banner("Environment Check")
    out = capsys.readouterr().out
    assert "Environment Check" in out
    assert "---" in out


def test_section_is_plain_without_decorative_fragments(capsys) -> None:
    set_color_enabled(True)
    try:
        output.section("Example Section")
        out = strip_markup(capsys.readouterr().out)
    finally:
        set_color_enabled(None)
    assert "Example Section" in out
    assert "╭" not in out
    assert "╯" not in out


def test_bullet_uses_plain_dash_even_with_color(capsys) -> None:
    set_color_enabled(True)
    try:
        output.bullet("safe note")
        out = strip_markup(capsys.readouterr().out)
    finally:
        set_color_enabled(None)
    assert "- safe note" in out
    assert "◆" not in out
