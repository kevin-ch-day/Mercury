"""Tests for pure display formatting helpers."""

from mercury import display_format


def test_format_bytes_scales() -> None:
    assert display_format.format_bytes(512) == "512 B"
    assert "KiB" in display_format.format_bytes(2048)
    assert "MiB" in display_format.format_bytes(5 * 1024 * 1024)


def test_short_path_truncates_long_paths() -> None:
    path = "/very/long/path/" + "x" * 80
    short = display_format.short_path(path, max_len=20)
    assert short.startswith("…")
    assert len(short) == 20


def test_format_pair() -> None:
    assert display_format.format_pair("a_prod", "a_dev") == "a_prod -> a_dev"


def test_format_count_summary() -> None:
    text = display_format.format_count_summary(verified=2, missing=1, failed=0)
    assert text == "2 verified, 1 missing, 0 failed"


def test_format_plan_status() -> None:
    assert display_format.format_plan_status(ready=True) == "ready"
    assert display_format.format_plan_status(ready=False, blockers=["no backup"]) == "no backup"
    assert display_format.format_plan_status(ready=False) == "blocked"


def test_format_table_aligns_columns() -> None:
    lines = display_format.format_table(
        ["DATABASE", "ENV"],
        [
            ["erebus_threat_intel_prod", "PROD"],
            ["erebus_threat_intel_dev", "DEV"],
        ],
    )
    assert len(lines) == 4
    assert "DATABASE" in lines[0]
    assert lines[1].startswith("  -")


def test_format_report_header() -> None:
    lines = display_format.format_report_header("BACKUP LIST")
    assert lines[0] == "BACKUP LIST"
    assert lines[1] == "-" * 16
