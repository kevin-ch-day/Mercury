"""Tests for uniform table formatting."""

from mercury import display_format, display_table, display_screen


def test_format_table_aligns_columns() -> None:
    lines = display_table.format_table(
        ["DATABASE", "ENV"],
        [
            ["erebus_threat_intel_prod", "PROD"],
            ["erebus_threat_intel_dev", "DEV"],
        ],
    )
    assert len(lines) == 4
    assert "DATABASE" in lines[0]
    assert "ENV" in lines[0]
    assert lines[1].startswith("  -")
    assert "erebus_threat_intel_prod" in lines[2]


def test_format_table_empty_rows() -> None:
    lines = display_table.format_table(["A"], [])
    assert lines == ["  (none)"]


def test_format_table_truncates_wide_cells() -> None:
    long_pair = "erebus_threat_intel_prod -> erebus_threat_intel_dev"
    lines = display_table.format_table(
        ["PAIR", "STATUS"],
        [[long_pair, "blocked"]],
        max_col_widths=[30, 10],
    )
    body = lines[2]
    assert "…" in body
    assert len(body) <= 50


def test_table_builder() -> None:
    table = display_table.Table.from_headers(
        ["DATABASE", "ENV"],
        min_col_widths=[24, 8],
        max_col_widths=[40, 8],
    )
    table.add_row("erebus_threat_intel_prod", "PROD")
    lines = table.lines()
    assert "DATABASE" in lines[0]
    assert "erebus_threat_intel_prod" in lines[2]


def test_format_table_respects_min_column_widths() -> None:
    lines = display_table.format_table(
        ["ROLE", "PLAN"],
        [["prod", "backup"]],
        indent=0,
        min_col_widths=[8, 10],
    )
    assert lines[0] == "ROLE      PLAN"
    assert lines[2] == "prod      backup"


def test_write_table_delegates_to_display_table(capsys) -> None:
    display_screen.write_table(
        ["A", "B"],
        [["1", "22"]],
        max_col_widths=[10, 10],
    )
    out = capsys.readouterr().out
    assert "A" in out
    assert "22" in out


def test_write_structured_table_uses_table_builder(capsys) -> None:
    table = display_table.Table.from_headers(
        ["DATABASE", "STATUS"],
        [["android_permission_intel", "current"]],
        min_col_widths=[24, 8],
    )
    display_screen.write_structured_table(table)
    out = capsys.readouterr().out
    assert "DATABASE" in out
    assert "android_permission_intel" in out
    assert "current" in out


def test_display_format_reexports_format_table() -> None:
    lines = display_format.format_table(["X"], [["y"]])
    assert "X" in lines[0]

# merged from test_display_screen.py
def test_write_status_tags(capsys) -> None:
    display_screen.write_status("ok", "verified")
    display_screen.write_status("warn", "missing")
    out = capsys.readouterr().out
    assert "[ok]" in out
    assert "[--]" in out

# merged from test_display_screen.py
def test_write_count_header(capsys) -> None:
    display_screen.write_count_header(ready=2, blocked=1)
    out = capsys.readouterr().out
    assert "2 ready, 1 blocked" in out

# merged from test_display_screen.py
def test_write_report_header(capsys) -> None:
    display_screen.write_report_header("BACKUP LIST")
    out = capsys.readouterr().out
    assert "BACKUP LIST" in out
    assert "─" in out

