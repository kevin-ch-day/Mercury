"""
Shared screen rendering for Mercury terminal output.

Compact menu screens and CLI reports should use these helpers so sections,
tables, status rows, and headers look the same everywhere.
"""

from __future__ import annotations

from typing import Literal

from mercury.core import output
from mercury.terminal.table import format_table
from mercury.terminal.theme import open_screen_lines, tag

StatusKind = Literal["ok", "warn", "fail", "info"]


def open_screen(title: str) -> None:
    """Menu action title with a full-width rule."""
    for line in open_screen_lines(title):
        output.write(line)


def write_report_header(title: str) -> None:
    """Verbose report title block."""
    output.write_report_header(title)


def write_summary(text: str) -> None:
    from mercury.terminal.theme import summary_line

    output.write(summary_line(text))


def write_section(title: str) -> None:
    output.section(title)


def write_fields(fields: dict[str, object]) -> None:
    for name, value in fields.items():
        output.field(name, value)


def write_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    indent: int = 2,
    min_col_widths: list[int] | None = None,
    max_col_widths: list[int] | None = None,
    align: list[str] | None = None,
) -> None:
    from mercury.terminal.table import TableStyle, format_table
    from mercury.terminal.theme import style_table_lines

    lines = format_table(
        headers,
        rows,
        indent=indent,
        min_col_widths=min_col_widths,
        max_col_widths=max_col_widths,
        align=align,
        style=TableStyle(indent=indent),
    )
    for line in style_table_lines(lines):
        output.write(line)


def write_compact_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    min_col_widths: list[int] | None = None,
    max_col_widths: list[int] | None = None,
    align: list[str] | None = None,
) -> None:
    """Shared zero-indent operator table with fixed-width columns."""
    write_table(
        headers,
        rows,
        indent=0,
        min_col_widths=min_col_widths,
        max_col_widths=max_col_widths,
        align=align,
    )


def write_list(title: str, items: list[str]) -> None:
    write_section(title)
    if not items:
        output.item("(none)")
        return
    for item in items:
        output.item(item)


def write_status(kind: StatusKind, text: str) -> None:
    output.item(tag(kind, text))


def write_hint(text: str) -> None:
    output.write_hint(text)


def write_bullets(items: list[str]) -> None:
    for item in items:
        output.bullet(item)


def write_blank() -> None:
    output.write()


def write_count_header(**counts: int) -> None:
    """One-line count summary at the top of a screen."""
    from mercury.terminal.format import format_count_summary
    from mercury.terminal.theme import count_summary_line

    write_summary(count_summary_line(format_count_summary(**counts)))


def write_footer_note(text: str) -> None:
    write_blank()
    from mercury.terminal.theme import hint_text

    output.write(hint_text(text))
