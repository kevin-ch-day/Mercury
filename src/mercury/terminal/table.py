"""
Uniform text-table formatting for Mercury CLI output.

All tabular output should go through this module (via ``display_screen.write_table``
or ``format_table``) so columns, rules, indentation, and truncation stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from mercury.terminal.format import short_path

Align = Literal["left", "right"]

DEFAULT_INDENT = 2
DEFAULT_GAP = 2
DEFAULT_RULE_CHAR = "-"
DEFAULT_EMPTY_LABEL = "(none)"


@dataclass(frozen=True)
class TableStyle:
    """Shared table layout defaults for Mercury screens and CLI reports."""

    indent: int = DEFAULT_INDENT
    gap: int = DEFAULT_GAP
    rule_char: str = DEFAULT_RULE_CHAR
    empty_label: str = DEFAULT_EMPTY_LABEL


@dataclass(frozen=True)
class TableColumn:
    """Column definition with optional width cap and alignment."""

    header: str
    min_width: int = 0
    max_width: int | None = None
    align: Align = "left"


@dataclass
class Table:
    """Structured table builder — prefer for new code."""

    columns: list[TableColumn]
    rows: list[list[str]] = field(default_factory=list)
    style: TableStyle = field(default_factory=TableStyle)

    @classmethod
    def from_headers(
        cls,
        headers: list[str],
        rows: list[list[str]] | None = None,
        *,
        min_col_widths: list[int] | None = None,
        max_col_widths: list[int] | None = None,
        style: TableStyle | None = None,
    ) -> Table:
        min_widths = min_col_widths or []
        widths = max_col_widths or []
        columns = [
            TableColumn(
                header=header,
                min_width=min_widths[index] if index < len(min_widths) else 0,
                max_width=widths[index] if index < len(widths) else None,
            )
            for index, header in enumerate(headers)
        ]
        return cls(columns=columns, rows=list(rows or []), style=style or TableStyle())

    def add_row(self, *cells: str) -> None:
        self.rows.append([str(cell) for cell in cells])

    def lines(self) -> list[str]:
        headers = [column.header for column in self.columns]
        min_widths = [column.min_width for column in self.columns]
        max_widths = [column.max_width for column in self.columns]
        align = [column.align for column in self.columns]
        return format_table(
            headers,
            self.rows,
            style=self.style,
            min_col_widths=min_widths,
            max_col_widths=max_widths,
            align=align,
        )


def truncate_cell(value: str, *, max_width: int) -> str:
    """Truncate a cell value; paths use ``short_path`` when they look like paths."""
    text = str(value)
    if max_width <= 0 or len(text) <= max_width:
        return text
    if "/" in text or text.startswith("…"):
        return short_path(text, max_len=max_width)
    if max_width <= 1:
        return "…"
    return f"…{text[-(max_width - 1):]}"


def _normalize_rows(
    headers: list[str],
    rows: list[list[str]],
    *,
    max_col_widths: list[int] | None,
) -> list[list[str]]:
    normalized: list[list[str]] = []
    for row in rows:
        cells = [(row[i] if i < len(row) else "") for i in range(len(headers))]
        if max_col_widths:
            capped: list[str] = []
            for index, cell in enumerate(cells):
                limit = max_col_widths[index] if index < len(max_col_widths) else None
                capped.append(truncate_cell(cell, max_width=limit) if limit else cell)
            cells = capped
        normalized.append(cells)
    return normalized


def _pad_cell(cell: str, width: int, *, align: Align) -> str:
    if align == "right":
        return cell.rjust(width)
    return cell.ljust(width)


def format_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    indent: int | None = None,
    gap: int | None = None,
    min_col_widths: list[int] | None = None,
    max_col_widths: list[int] | None = None,
    align: list[Align] | None = None,
    style: TableStyle | None = None,
) -> list[str]:
    """
    Render an aligned text table as lines (no trailing blank line).

    Parameters
    ----------
    headers:
        Column titles.
    rows:
        Body rows; short rows are padded with empty strings.
    min_col_widths:
        Optional per-column minimum widths to keep compact operator tables aligned.
    max_col_widths:
        Optional per-column character limits (cells truncated with ``…``).
    align:
        Optional per-column alignment (``left`` or ``right``).
    style:
        Layout overrides (indent, gap, rule character, empty label).
    """
    resolved = style or TableStyle()
    prefix = " " * (indent if indent is not None else resolved.indent)
    column_gap = gap if gap is not None else resolved.gap
    gap_text = " " * column_gap

    if not headers:
        return [f"{prefix}{resolved.empty_label}"]

    if not rows:
        return [f"{prefix}{resolved.empty_label}"]

    alignments = align or ["left"] * len(headers)
    normalized_rows = _normalize_rows(headers, rows, max_col_widths=max_col_widths)

    min_widths = min_col_widths or []
    widths = [
        max(len(header), min_widths[index] if index < len(min_widths) else 0)
        for index, header in enumerate(headers)
    ]
    for cells in normalized_rows:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    header_cells = [
        _pad_cell(header, widths[i], align=alignments[i] if i < len(alignments) else "left")
        for i, header in enumerate(headers)
    ]
    header_line = prefix + gap_text.join(header_cells)
    rule = prefix + resolved.rule_char * max(len(header_line) - len(prefix), 1)

    body: list[str] = []
    for cells in normalized_rows:
        padded = [
            _pad_cell(
                cells[i],
                widths[i],
                align=alignments[i] if i < len(alignments) else "left",
            )
            for i in range(len(headers))
        ]
        body.append(prefix + gap_text.join(padded))

    return [header_line, rule, *body]
