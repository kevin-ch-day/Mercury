"""Tests for shared screen rendering helpers."""

from mercury import display_format, display_screen


def test_write_table_renders_aligned_columns(capsys) -> None:
    display_screen.write_table(
        ["DATABASE", "ENV"],
        [
            ["erebus_threat_intel_prod", "PROD"],
            ["erebus_threat_intel_dev", "DEV"],
        ],
    )
    out = capsys.readouterr().out
    assert "DATABASE" in out
    assert "erebus_threat_intel_prod" in out


def test_write_status_tags(capsys) -> None:
    display_screen.write_status("ok", "verified")
    display_screen.write_status("warn", "missing")
    out = capsys.readouterr().out
    assert "[ok]" in out
    assert "[--]" in out


def test_write_count_header(capsys) -> None:
    display_screen.write_count_header(ready=2, blocked=1)
    out = capsys.readouterr().out
    assert "2 ready, 1 blocked" in out


def test_write_report_header(capsys) -> None:
    display_screen.write_report_header("BACKUP LIST")
    out = capsys.readouterr().out
    assert "BACKUP LIST" in out
    assert "─" in out


def test_write_table_uses_shared_formatter(capsys) -> None:
    headers = ["A", "B"]
    rows = [["1", "22"]]
    display_screen.write_table(headers, rows)
    out = capsys.readouterr().out
    assert "A" in out
    assert "22" in out
    assert out.count("-") >= 1
