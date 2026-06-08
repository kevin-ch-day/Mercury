"""Tests for Mercury terminal theme."""

from mercury.terminal.theme import (
    fancy_rule,
    menu_title_line,
    rule_line,
    set_color_enabled,
    strip_markup,
    tag,
    tag_plain,
)


def test_tag_plain_always_ascii() -> None:
    assert tag_plain("ok", "done") == "[ok] done"
    assert tag_plain("warn", "dry-run") == "[--] dry-run"
    assert tag_plain("fail", "error") == "[!!] error"


def test_tag_respects_color_override() -> None:
    set_color_enabled(False)
    assert tag("ok", "done") == "[ok] done"
    set_color_enabled(True)
    styled = tag("ok", "done")
    assert styled != "[ok] done"
    assert "done" in strip_markup(styled)
    set_color_enabled(None)


def test_menu_title_contains_mercury() -> None:
    set_color_enabled(False)
    assert "MERCURY" in menu_title_line()
    set_color_enabled(None)


def test_menu_title_line_has_mercury_glyph_when_colored() -> None:
    from mercury.terminal.theme import menu_title_line, set_color_enabled

    set_color_enabled(True)
    assert "MERCURY" in strip_markup(menu_title_line())
    set_color_enabled(None)


def test_dashboard_panel_plain_mode() -> None:
    from mercury.terminal.theme import dashboard_panel, set_color_enabled

    set_color_enabled(False)
    rows = ["  Mode                  [--] dry-run"]
    assert dashboard_panel(rows) == rows
    set_color_enabled(None)


def test_style_table_cell_highlights_status() -> None:
    from mercury.terminal.theme import set_color_enabled, strip_markup, style_table_cell

    set_color_enabled(True)
    assert "ready" in strip_markup(style_table_cell("ready"))
    assert "blocked" in strip_markup(style_table_cell("blocked"))
    set_color_enabled(None)


def test_strip_markup_removes_rich_tags() -> None:
    set_color_enabled(True)
    styled = tag("fail", "boom")
    plain = strip_markup(styled)
    assert "boom" in plain
    assert "#FF6B81" not in plain
    set_color_enabled(None)


def test_fancy_rule_is_plain_rule_without_box_fragments() -> None:
    set_color_enabled(True)
    try:
        plain = strip_markup(fancy_rule(width=20))
        assert plain == strip_markup(rule_line(width=20))
        assert "╭" not in plain
        assert "╯" not in plain
    finally:
        set_color_enabled(None)


def test_style_table_lines_preserve_body_alignment_when_colored() -> None:
    from mercury.terminal.table import format_table
    from mercury.terminal.theme import style_table_lines

    lines = format_table(
        ["DATABASE", "ROLE", "PLAN", "SYNC"],
        [
            ["android_permission_intel", "shared", "backup", "n/a"],
            ["erebus_threat_intel_prod", "prod", "backup", "dev target"],
        ],
        indent=0,
    )

    set_color_enabled(True)
    try:
        styled = style_table_lines(lines)
        plain = [strip_markup(line) for line in styled]
        assert plain[2] == lines[2]
        assert plain[3] == lines[3]
    finally:
        set_color_enabled(None)
